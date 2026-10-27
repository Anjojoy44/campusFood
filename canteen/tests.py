from io import BytesIO
from datetime import datetime, time

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from unittest.mock import patch
from django.utils import timezone
from openpyxl import load_workbook

from canteen.forms import MenuItemForm, FoodOfferForm, CustomUserCreationForm
from canteen.models import FoodOffer, FoodOrder, MenuItem, OrderNotification, UserFeedback, UserProfile
from canteen.views import can_cancel_order, is_menu_ordering_open, menu_update_window_open


class FoodManagementFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='customer', password='TestPass123')

    def test_menu_item_form_validates_required_fields(self):
        form = MenuItemForm(data={
            'name': 'Idli',
            'description': 'Soft steamed rice cakes',
            'category': 'Breakfast',
            'meal_type': 'breakfast',
            'price': '40.00',
            'available_quantity': 25,
            'date': '2026-09-03',
            'opening_time': '2026-09-02T08:00',
            'closing_time': '2026-09-03T18:00',
            'availability_status': 'available',
        })

        self.assertTrue(form.is_valid(), form.errors)

    def test_order_is_auto_approved_when_menu_allows_it(self):
        UserProfile.objects.create(user=self.user, admission_id='STU-123')
        staff_user = User.objects.create_user(username='canteen-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        item = MenuItem.objects.create(
            name='Auto Approved Meal', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=60, available_quantity=10,
            date=timezone.localdate(), availability_status='available',
            opening_time=timezone.make_aware(datetime.combine(
                timezone.localdate() - timezone.timedelta(days=1), time(8, 0)
            )),
            closing_time=timezone.make_aware(datetime.combine(
                timezone.localdate() + timezone.timedelta(days=1), time(21, 0)
            )),
            auto_approve_orders=True,
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse('place_order_for_menu', args=[item.id]), {
            'menu_item': item.id,
            'quantity': 2,
            'delivery_date': timezone.localdate(),
            'notes': 'Please pack well',
        })

        self.assertEqual(response.status_code, 302)
        order = FoodOrder.objects.get(user=self.user, menu_item=item)
        self.assertEqual(order.status, 'confirmed')
        self.assertEqual(
            set(OrderNotification.objects.filter(order=order).values_list('recipient_id', flat=True)),
            {self.user.id, staff_user.id},
        )
        staff_notification = OrderNotification.objects.get(order=order, recipient=staff_user)
        self.assertIn('STU-123', staff_notification.message)
        self.assertIn('New order', staff_notification.message)
        self.assertIn('Auto Approved Meal', staff_notification.message)
        self.assertContains(self.client.get(reverse('my_orders')), 'Your order')
        item.refresh_from_db()
        self.assertEqual(item.available_quantity, 8)

    def test_staff_confirmation_notifies_user_and_staff_with_admission_number(self):
        UserProfile.objects.create(user=self.user, admission_id='STU-456')
        staff_user = User.objects.create_user(username='canteen-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        item = MenuItem.objects.create(
            name='Lunch Meal', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=60, available_quantity=10,
            date=timezone.localdate(), availability_status='available',
        )
        order = FoodOrder.objects.create(
            user=self.user,
            menu_item=item,
            quantity=2,
            total_price=120,
            delivery_date=timezone.localdate(),
            status='pending',
        )
        self.client.force_login(staff_user)

        response = self.client.post(reverse('staff_orders'), {
            'order_id': order.id,
            'status': 'confirmed',
        })

        self.assertRedirects(response, reverse('staff_orders'))
        self.assertEqual(OrderNotification.objects.filter(order=order).count(), 1)
        self.assertTrue(
            OrderNotification.objects.filter(
                order=order,
                recipient=self.user,
                message__contains='has been confirmed',
            ).exists()
        )
        self.assertFalse(OrderNotification.objects.filter(order=order, recipient=staff_user).exists())
        self.assertContains(self.client.get(reverse('staff_orders')), 'STU-456')

    def test_staff_can_search_orders_by_customer_admission_item_and_order_id(self):
        UserProfile.objects.create(user=self.user, admission_id='STU-SEARCH')
        item = MenuItem.objects.create(
            name='Searchable Lunch', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=60, available_quantity=10,
            date=timezone.localdate(), availability_status='available',
        )
        matching_order = FoodOrder.objects.create(
            user=self.user, menu_item=item, quantity=1, total_price=60,
            delivery_date=timezone.localdate(),
        )
        other_user = User.objects.create_user(username='other-customer', password='TestPass123')
        FoodOrder.objects.create(
            user=other_user, quantity=1, total_price=50,
            delivery_date=timezone.localdate(),
        )
        staff_user = User.objects.create_user(username='search-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        self.client.force_login(staff_user)

        for search_term in ('STU-SEARCH', 'Searchable Lunch', str(matching_order.id)):
            with self.subTest(search_term=search_term):
                response = self.client.get(reverse('staff_orders'), {'q': search_term})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    list(response.context['orders'].values_list('id', flat=True)),
                    [matching_order.id],
                )
                self.assertContains(response, f'value="{search_term}"')

    def test_staff_can_filter_all_orders_by_status_and_keep_keyword_search(self):
        UserProfile.objects.create(user=self.user, admission_id='STU-STATUS')
        item = MenuItem.objects.create(
            name='Status Filter Meal', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=60, available_quantity=10,
            date=timezone.localdate(), availability_status='available',
        )
        matching_order = FoodOrder.objects.create(
            user=self.user, menu_item=item, quantity=1, total_price=60,
            delivery_date=timezone.localdate(), status='ready',
        )
        FoodOrder.objects.create(
            user=self.user, menu_item=item, quantity=1, total_price=60,
            delivery_date=timezone.localdate(), status='pending',
        )
        staff_user = User.objects.create_user(username='status-filter-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        self.client.force_login(staff_user)

        response = self.client.get(reverse('staff_orders'), {
            'q': 'Status Filter Meal',
            'status': 'ready',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['orders'].values_list('id', flat=True)),
            [matching_order.id],
        )
        self.assertEqual(response.context['selected_status'], 'ready')
        self.assertContains(response, 'value="ready" selected')

    def test_staff_can_choose_specific_order_search_field(self):
        UserProfile.objects.create(user=self.user, admission_id='STU-FIELD-SEARCH')
        matching_order = FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=60,
            delivery_date=timezone.localdate(), status='ready',
        )
        other_user = User.objects.create_user(
            username='different-admission', password='TestPass123',
        )
        UserProfile.objects.create(user=other_user, admission_id='OTHER-ID')
        FoodOrder.objects.create(
            user=other_user, quantity=1, total_price=50,
            delivery_date=timezone.localdate(), status='ready',
        )
        staff_user = User.objects.create_user(username='field-search-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        self.client.force_login(staff_user)

        response = self.client.get(reverse('staff_orders'), {
            'q': 'STU-FIELD-SEARCH',
            'search_by': 'admission',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['orders'].values_list('id', flat=True)),
            [matching_order.id],
        )
        self.assertEqual(response.context['search_by'], 'admission')
        self.assertContains(response, 'value="admission" selected')

    def test_staff_order_search_rejects_unknown_search_field(self):
        staff_user = User.objects.create_user(username='invalid-search-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        self.client.force_login(staff_user)

        response = self.client.get(reverse('staff_orders'), {'search_by': 'unexpected'})

        self.assertEqual(response.status_code, 400)

    def test_staff_order_status_filter_rejects_unknown_status(self):
        staff_user = User.objects.create_user(username='invalid-status-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        self.client.force_login(staff_user)

        response = self.client.get(reverse('staff_orders'), {'status': 'unknown'})

        self.assertEqual(response.status_code, 400)

    def test_customer_can_cancel_order_before_cutoff_and_stock_is_restored(self):
        now = timezone.now()
        item = MenuItem.objects.create(
            name='Cancelable Meal', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=60, available_quantity=5,
            date=timezone.localdate(),
            opening_time=now - timezone.timedelta(hours=2),
            closing_time=now + timezone.timedelta(hours=1),
        )
        order = FoodOrder.objects.create(
            user=self.user, menu_item=item, quantity=3, total_price=180,
            delivery_date=timezone.localdate(), status='confirmed',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('my_orders'))
        self.assertContains(response, 'Cancel')
        response = self.client.post(reverse('cancel_order', args=[order.id]))

        self.assertRedirects(response, reverse('my_orders'))
        order.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertEqual(item.available_quantity, 8)

    def test_customer_cannot_cancel_after_cutoff_or_cancel_another_users_order(self):
        now = timezone.now()
        item = MenuItem.objects.create(
            name='Closed Meal', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=60, available_quantity=5,
            date=timezone.localdate(),
            opening_time=now - timezone.timedelta(hours=2),
            closing_time=now - timezone.timedelta(seconds=1),
        )
        order = FoodOrder.objects.create(
            user=self.user, menu_item=item, quantity=2, total_price=120,
            delivery_date=timezone.localdate(),
        )
        other_user = User.objects.create_user(username='other-owner', password='TestPass123')
        another_order = FoodOrder.objects.create(
            user=other_user, menu_item=item, quantity=1, total_price=60,
            delivery_date=timezone.localdate(),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('my_orders'))
        self.assertNotContains(response, f'action="{reverse("cancel_order", args=[order.id])}"')
        self.client.post(reverse('cancel_order', args=[order.id]))
        self.assertEqual(
            self.client.post(reverse('cancel_order', args=[another_order.id])).status_code,
            404,
        )
        order.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertEqual(item.available_quantity, 5)

    def test_order_cancellation_cutoff_is_exclusive(self):
        now = timezone.now()
        item = MenuItem.objects.create(
            name='Cutoff Meal', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=60, available_quantity=5,
            date=timezone.localdate(),
            closing_time=now,
        )
        order = FoodOrder.objects.create(
            user=self.user, menu_item=item, quantity=1, total_price=60,
            delivery_date=timezone.localdate(),
        )

        self.assertFalse(can_cancel_order(order, now=now))

    def test_staff_can_view_orders_filtered_by_delivery_date(self):
        UserProfile.objects.create(user=self.user, admission_id='STU-DATE')
        staff_user = User.objects.create_user(username='date-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        today = timezone.localdate()
        today_order = FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=50, delivery_date=today,
        )
        FoodOrder.objects.create(
            user=self.user, quantity=2, total_price=100,
            delivery_date=today + timezone.timedelta(days=1),
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse('staff_orders_by_date'), {'date': today.isoformat()})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{today_order.id}')
        self.assertContains(response, 'Download Excel')
        self.assertContains(response, 'Download PDF')
        self.assertEqual(
            list(response.context['orders'].values_list('id', flat=True)),
            [today_order.id],
        )
        self.assertContains(response, 'STU-DATE')

    def test_staff_date_order_view_defaults_to_today(self):
        staff_user = User.objects.create_user(username='today-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        today_order = FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=50, delivery_date=timezone.localdate(),
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse('staff_orders_by_date'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{today_order.id}')

    def test_staff_can_filter_orders_by_status_for_delivery_date(self):
        staff_user = User.objects.create_user(username='status-filter-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        today = timezone.localdate()
        cancelled_order = FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=50,
            delivery_date=today, status='cancelled',
        )
        FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=50,
            delivery_date=today, status='confirmed',
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse('staff_orders_by_date'), {
            'date': today.isoformat(),
            'status': 'cancelled',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['orders'].values_list('id', flat=True)),
            [cancelled_order.id],
        )
        self.assertContains(response, 'Cancelled')

    def test_staff_date_order_view_rejects_invalid_date(self):
        staff_user = User.objects.create_user(username='invalid-date-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        self.client.force_login(staff_user)

        response = self.client.get(reverse('staff_orders_by_date'), {'date': 'not-a-date'})

        self.assertEqual(response.status_code, 400)

    def test_staff_can_view_orders_filtered_by_delivery_month_and_status(self):
        UserProfile.objects.create(user=self.user, admission_id='STU-MONTH')
        staff_user = User.objects.create_user(username='month-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        month_start = timezone.localdate().replace(day=1)
        selected_order = FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=50,
            delivery_date=month_start, status='cancelled',
        )
        FoodOrder.objects.create(
            user=self.user, quantity=2, total_price=100,
            delivery_date=month_start, status='confirmed',
        )
        FoodOrder.objects.create(
            user=self.user, quantity=3, total_price=150,
            delivery_date=month_start - timezone.timedelta(days=1),
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse('staff_orders_by_month'), {
            'month': month_start.strftime('%Y-%m'),
            'status': 'cancelled',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['orders'].values_list('id', flat=True)),
            [selected_order.id],
        )
        self.assertContains(response, 'STU-MONTH')

    def test_staff_month_order_view_rejects_invalid_month(self):
        staff_user = User.objects.create_user(username='invalid-month-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        self.client.force_login(staff_user)

        response = self.client.get(reverse('staff_orders_by_month'), {'month': '2026-13'})

        self.assertEqual(response.status_code, 400)

    def test_pending_order_placement_notifies_staff_before_confirmation(self):
        UserProfile.objects.create(user=self.user, admission_id='STU-PLACED')
        staff_user = User.objects.create_user(username='new-order-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        item = MenuItem.objects.create(
            name='Pending Meal', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=60, available_quantity=10,
            date=timezone.localdate(), availability_status='available',
            opening_time=timezone.make_aware(datetime.combine(
                timezone.localdate() - timezone.timedelta(days=1), time(8, 0)
            )),
            closing_time=timezone.make_aware(datetime.combine(
                timezone.localdate() + timezone.timedelta(days=1), time(21, 0)
            )),
        )
        self.client.force_login(self.user)

        response = self.client.post(reverse('place_order_for_menu', args=[item.id]), {
            'menu_item': item.id,
            'quantity': 1,
            'delivery_date': timezone.localdate(),
        })

        self.assertEqual(response.status_code, 302)
        order = FoodOrder.objects.get(user=self.user, menu_item=item)
        self.assertEqual(order.status, 'pending')
        staff_notification = OrderNotification.objects.get(
            recipient=staff_user,
            order=order,
        )
        self.assertIn('New order', staff_notification.message)
        self.assertIn('STU-PLACED', staff_notification.message)
        self.assertFalse(OrderNotification.objects.filter(recipient=self.user, order=order).exists())

    def test_order_window_can_open_on_previous_day(self):
        item = MenuItem.objects.create(
            name='Previous Day Order', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=70, available_quantity=12,
            date=timezone.localdate(), availability_status='available',
            opening_time=timezone.make_aware(datetime.combine(
                timezone.localdate() - timezone.timedelta(days=1), time(20, 0)
            )),
            closing_time=timezone.make_aware(datetime.combine(timezone.localdate(), time(18, 0))),
        )

        start_dt = timezone.make_aware(datetime.combine(item.date - timezone.timedelta(days=1), time(20, 0)))
        middle_dt = timezone.make_aware(datetime.combine(item.date, time(12, 0)))
        close_dt = timezone.make_aware(datetime.combine(item.date, time(18, 0)))

        self.assertTrue(is_menu_ordering_open(item, now=start_dt))
        self.assertTrue(is_menu_ordering_open(item, now=middle_dt))
        self.assertFalse(is_menu_ordering_open(item, now=close_dt))

    def test_order_window_allows_noon_booking_in_canteen_timezone(self):
        item = MenuItem.objects.create(
            name='Briyanni', description='Lunch meal', category='Lunch',
            meal_type='lunch', price=80, available_quantity=10,
            date=datetime(2026, 10, 2).date(), availability_status='available',
            opening_time=timezone.make_aware(datetime(2026, 10, 1, 10, 0)),
            closing_time=timezone.make_aware(datetime(2026, 10, 2, 8, 0)),
        )

        noon_local = timezone.make_aware(datetime(2026, 10, 1, 12, 0))
        self.client.force_login(self.user)
        with patch('canteen.views.timezone.localtime', return_value=noon_local):
            response = self.client.post(reverse('place_order_for_menu', args=[item.id]), {
                'menu_item': item.id,
                'quantity': 1,
                'delivery_date': item.date,
                'notes': '',
            })

        self.assertRedirects(response, reverse('my_orders'))
        self.assertTrue(FoodOrder.objects.filter(user=self.user, menu_item=item).exists())

    def test_menu_upload_window_is_not_limited_to_9_am(self):
        self.assertTrue(menu_update_window_open(now=timezone.make_aware(datetime(2026, 9, 10, 18, 0))))

    def test_menu_item_form_allows_previous_day_opening_time(self):
        form = MenuItemForm(data={
            'name': 'Previous Day Menu',
            'description': 'Food as per delivery day',
            'category': 'Lunch',
            'meal_type': 'lunch',
            'price': '80.00',
            'available_quantity': 10,
            'date': '2026-09-10',
            'opening_time': '2026-09-09T20:00',
            'closing_time': '2026-09-10T18:00',
            'auto_approve_orders': True,
            'availability_status': 'available',
        })

        self.assertTrue(form.is_valid(), form.errors)

    def test_canteen_staff_can_create_menu_item(self):
        UserProfile.objects.create(user=self.user, user_type='canteen_staff')
        self.client.force_login(self.user)
        menu_date = timezone.localdate()
        response = self.client.post(reverse('staff_menu'), {
            'name': 'New Lunch',
            'description': 'Freshly prepared lunch',
            'category': 'Lunch',
            'meal_type': 'lunch',
            'price': '75.00',
            'available_quantity': 20,
            'date': menu_date.isoformat(),
            'opening_time': f'{menu_date.isoformat()}T08:00',
            'closing_time': f'{menu_date.isoformat()}T18:00',
            'availability_status': 'available',
        })

        self.assertRedirects(response, reverse('staff_menu'))
        self.assertTrue(MenuItem.objects.filter(name='New Lunch').exists())

    def test_homepage_shows_uploaded_menu_in_daily_menu_section(self):
        MenuItem.objects.create(
            name='Briyanni', description='Lunch special', category='Lunch',
            meal_type='lunch', price=80, available_quantity=10,
            date=timezone.localdate() + timezone.timedelta(days=1),
            availability_status='available',
        )

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'id="menu"')
        self.assertContains(response, 'Briyanni')

    def test_offer_form_rejects_invalid_discount_price(self):
        item = MenuItem.objects.create(
            name='Offer Menu Item', description='Meal', category='Lunch',
            meal_type='lunch', price=120, available_quantity=10,
            date=timezone.localdate(), availability_status='available',
        )
        form = FoodOfferForm(data={
            'menu_item': item.id,
            'name': 'Lunch Combo',
            'description': 'Combo meal offer',
            'original_price': '120.00',
            'discounted_price': '120.00',
            'discount_percentage': 10,
            'available_quantity': 10,
            'offer_date': '2026-09-03',
            'start_time': '13:30',
            'end_time': '2026-09-03T15:00',
            'status': 'active',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('discounted_price', form.errors)

    def test_canteen_staff_can_create_offer_outside_previous_window(self):
        UserProfile.objects.create(user=self.user, user_type='canteen_staff')
        self.client.force_login(self.user)
        offer_date = timezone.localdate()
        outside_window_time = timezone.make_aware(datetime.combine(offer_date, time(10, 0)))

        with patch('canteen.views.timezone.localtime', return_value=outside_window_time):
            response = self.client.post(reverse('staff_offers'), {
                'menu_item': '',
                'name': 'Anytime Offer',
                'description': 'Available for ordering today',
                'original_price': '100.00',
                'discounted_price': '80.00',
                'discount_percentage': 20,
                'available_quantity': 10,
                'offer_date': offer_date.isoformat(),
                'start_time': '13:30',
                'end_time': f'{offer_date.isoformat()}T15:00',
                'status': 'active',
            })

        self.assertRedirects(response, reverse('staff_offers'))
        self.assertTrue(FoodOffer.objects.filter(name='Anytime Offer').exists())

    def test_registration_form_excludes_admin_choice(self):
        form = CustomUserCreationForm()
        choices = dict(form.fields['user_type'].choices)
        self.assertNotIn('admin', choices)

    def test_registration_page_has_one_show_password_toggle_for_both_fields(self):
        response = self.client.get(reverse('register'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="registration-show-password"')
        self.assertContains(response, 'aria-controls="id_password1 id_password2"')
        self.assertEqual(response.content.count(b'Show password'), 1)
        self.assertContains(response, 'passwordInput.type = registrationPasswordToggle.checked ? \'text\' : \'password\'')

    def test_registration_form_validates_password_strength(self):
        data = {
            'first_name': 'Test',
            'last_name': 'User',
            'email': 'new@example.com',
            'user_type': 'student',
            'admission_id': 'STU-1',
            'username': 'newuser',
            'password1': 'weakpass',
            'password2': 'weakpass',
        }
        self.assertFalse(CustomUserCreationForm(data=data).is_valid())
        data['password1'] = data['password2'] = 'StrongPass1'
        self.assertFalse(CustomUserCreationForm(data=data).is_valid())
        data['password1'] = data['password2'] = 'StrongPass1!'
        form = CustomUserCreationForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.profile.admission_id, 'STU-1')

    def test_menu_requires_login_for_anonymous_user(self):
        response = self.client.get(reverse('menu_catalog'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_authenticated_customer_can_view_menu_and_orders(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('menu_catalog')).status_code, 200)
        self.assertEqual(self.client.get(reverse('my_orders')).status_code, 200)
        date_response = self.client.get(reverse('my_orders_by_date'))
        self.assertEqual(date_response.status_code, 200)
        self.assertContains(date_response, 'Download Excel')
        self.assertContains(date_response, 'Download PDF')
        month_response = self.client.get(reverse('my_orders_by_month'))
        self.assertEqual(month_response.status_code, 200)
        self.assertContains(month_response, 'Download Excel')
        self.assertContains(month_response, 'Download PDF')

    def test_customer_can_view_only_their_orders_for_selected_delivery_date(self):
        other_user = User.objects.create_user(username='another-date-customer', password='TestPass123')
        today = timezone.localdate()
        selected_order = FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=50, delivery_date=today,
        )
        FoodOrder.objects.create(
            user=self.user, quantity=2, total_price=100,
            delivery_date=today + timezone.timedelta(days=1),
        )
        FoodOrder.objects.create(
            user=other_user, quantity=3, total_price=150, delivery_date=today,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('my_orders_by_date'), {'date': today.isoformat()})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['orders'].values_list('id', flat=True)),
            [selected_order.id],
        )

    def test_customer_can_filter_orders_by_status_for_delivery_date(self):
        today = timezone.localdate()
        cancelled_order = FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=50,
            delivery_date=today, status='cancelled',
        )
        FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=50,
            delivery_date=today, status='confirmed',
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('my_orders_by_date'), {
            'date': today.isoformat(),
            'status': 'cancelled',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['orders'].values_list('id', flat=True)),
            [cancelled_order.id],
        )
        self.assertContains(response, 'Cancelled')

    def test_customer_date_orders_default_to_today_and_reject_invalid_date(self):
        today_order = FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=50, delivery_date=timezone.localdate(),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('my_orders_by_date'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['orders'].values_list('id', flat=True)),
            [today_order.id],
        )
        invalid_response = self.client.get(reverse('my_orders_by_date'), {'date': 'not-a-date'})
        self.assertEqual(invalid_response.status_code, 400)

    def test_customer_can_view_only_their_orders_for_selected_delivery_month(self):
        other_user = User.objects.create_user(username='another-month-customer', password='TestPass123')
        month_start = timezone.localdate().replace(day=1)
        next_month = (month_start.replace(day=28) + timezone.timedelta(days=4)).replace(day=1)
        selected_order = FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=50,
            delivery_date=month_start,
        )
        FoodOrder.objects.create(
            user=self.user, quantity=2, total_price=100,
            delivery_date=next_month,
        )
        FoodOrder.objects.create(
            user=other_user, quantity=3, total_price=150,
            delivery_date=month_start,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('my_orders_by_month'), {
            'month': month_start.strftime('%Y-%m'),
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['orders'].values_list('id', flat=True)),
            [selected_order.id],
        )

    def test_customer_month_orders_default_to_current_month_and_reject_invalid_month(self):
        today_order = FoodOrder.objects.create(
            user=self.user, quantity=1, total_price=50, delivery_date=timezone.localdate(),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('my_orders_by_month'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['orders'].values_list('id', flat=True)),
            [today_order.id],
        )
        invalid_response = self.client.get(reverse('my_orders_by_month'), {'month': 'not-a-month'})
        self.assertEqual(invalid_response.status_code, 400)

    def test_customer_can_download_filtered_monthly_excel_with_own_user_details(self):
        self.user.email = 'customer@example.com'
        self.user.save(update_fields=['email'])
        UserProfile.objects.create(
            user=self.user,
            admission_id='STU-REPORT',
            user_type='student',
        )
        other_user = User.objects.create_user(
            username='private-month-report-user',
            password='TestPass123',
            email='private@example.com',
        )
        UserProfile.objects.create(user=other_user, admission_id='OTHER-ID')
        month_start = timezone.localdate().replace(day=1)
        selected_order = FoodOrder.objects.create(
            user=self.user,
            quantity=2,
            total_price=120,
            delivery_date=month_start,
            status='confirmed',
            notes='No onion',
        )
        FoodOrder.objects.create(
            user=self.user,
            quantity=1,
            total_price=60,
            delivery_date=month_start,
            status='cancelled',
        )
        FoodOrder.objects.create(
            user=other_user,
            quantity=1,
            total_price=60,
            delivery_date=month_start,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('download_my_order_report', args=['month', 'xlsx']),
            {'month': month_start.strftime('%Y-%m'), 'status': 'confirmed'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('attachment;', response['Content-Disposition'])
        worksheet = load_workbook(BytesIO(response.content), read_only=True).active
        rows = list(worksheet.iter_rows(values_only=True))
        self.assertIn('Admission ID', rows[2])
        self.assertIn('Email', rows[2])
        self.assertEqual(rows[3][0], selected_order.id)
        self.assertEqual(rows[3][11], 'No onion')
        self.assertEqual(rows[3][13], self.user.username)
        self.assertEqual(rows[3][17], self.user.email)
        self.assertEqual(rows[3][18], 'STU-REPORT')
        self.assertEqual(len(rows), 4)

        daily_pdf = self.client.get(
            reverse('download_my_order_report', args=['date', 'pdf']),
            {'date': month_start.isoformat()},
        )
        self.assertEqual(daily_pdf.status_code, 200)
        self.assertEqual(daily_pdf['Content-Type'], 'application/pdf')
        self.assertTrue(daily_pdf.content.startswith(b'%PDF-'))

    def test_staff_can_download_daily_pdf_report(self):
        staff_user = User.objects.create_user(username='pdf-report-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        today = timezone.localdate()
        FoodOrder.objects.create(
            user=self.user,
            quantity=1,
            total_price=50,
            delivery_date=today,
        )
        self.client.force_login(staff_user)

        response = self.client.get(
            reverse('download_staff_order_report', args=['date', 'pdf']),
            {'date': today.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response['Content-Disposition'].endswith('.pdf"'))
        self.assertTrue(response.content.startswith(b'%PDF-'))

        monthly_excel = self.client.get(
            reverse('download_staff_order_report', args=['month', 'xlsx']),
            {'month': today.strftime('%Y-%m')},
        )
        self.assertEqual(monthly_excel.status_code, 200)
        self.assertEqual(
            monthly_excel['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    def test_staff_order_report_download_rejects_customers_and_invalid_filters(self):
        customer = User.objects.create_user(username='report-access-customer', password='TestPass123')
        self.client.force_login(customer)

        denied_response = self.client.get(
            reverse('download_staff_order_report', args=['month', 'xlsx']),
        )
        self.assertEqual(denied_response.status_code, 302)

        self.client.force_login(self.user)
        invalid_response = self.client.get(
            reverse('download_my_order_report', args=['month', 'pdf']),
            {'month': '2026-13'},
        )
        self.assertEqual(invalid_response.status_code, 400)

    def test_notifications_page_shows_only_current_users_notifications(self):
        staff_user = User.objects.create_user(username='canteen-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        order = FoodOrder.objects.create(
            user=self.user,
            quantity=1,
            total_price=60,
            delivery_date=timezone.localdate(),
        )
        OrderNotification.objects.create(
            recipient=self.user,
            order=order,
            message='Your order has been confirmed.',
        )
        OrderNotification.objects.create(
            recipient=staff_user,
            order=order,
            message='Customer admission number: STU-789.',
        )

        self.client.force_login(self.user)
        customer_response = self.client.get(reverse('notifications'))
        self.assertEqual(customer_response.status_code, 200)
        self.assertContains(customer_response, 'Your order has been confirmed.')
        self.assertNotContains(customer_response, 'STU-789')

        self.client.force_login(staff_user)
        staff_response = self.client.get(reverse('notifications'))
        self.assertEqual(staff_response.status_code, 200)
        self.assertContains(staff_response, 'STU-789')
        self.assertNotContains(staff_response, 'Your order has been confirmed.')
        self.assertContains(self.client.get(reverse('dashboard')), 'Notifications (1)')

    def test_staff_can_confirm_delivery_from_notifications_and_notify_customer(self):
        UserProfile.objects.create(user=self.user, admission_id='STU-789')
        staff_user = User.objects.create_user(username='delivery-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        order = FoodOrder.objects.create(
            user=self.user,
            quantity=2,
            total_price=120,
            delivery_date=timezone.localdate(),
            status='ready',
        )
        OrderNotification.objects.create(
            recipient=staff_user,
            order=order,
            message='New order #1 placed.',
        )
        self.client.force_login(staff_user)

        bypass_response = self.client.post(reverse('staff_orders'), {
            'order_id': order.id,
            'status': 'delivered',
        })
        self.assertRedirects(bypass_response, reverse('staff_orders'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'ready')

        response = self.client.get(reverse('notifications'))
        self.assertContains(response, 'Confirm delivery')
        response = self.client.post(reverse('confirm_order_delivery', args=[order.id]))

        self.assertRedirects(response, reverse('notifications'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')
        self.assertTrue(OrderNotification.objects.filter(
            recipient=self.user,
            order=order,
            message__contains='has been delivered',
        ).exists())
        self.assertFalse(OrderNotification.objects.filter(
            recipient=staff_user,
            order=order,
            message__contains='has been delivered',
        ).exists())
        self.assertContains(self.client.get(reverse('notifications')), 'Order status: Delivered')
        self.assertNotContains(self.client.get(reverse('notifications')), 'Confirm delivery')
        self.client.force_login(self.user)
        customer_notifications = self.client.get(reverse('notifications'))
        self.assertContains(customer_notifications, 'has been delivered')
        self.assertNotContains(customer_notifications, 'Confirm delivery')

    def test_staff_can_mark_non_ready_order_delivered_from_order_management(self):
        staff_user = User.objects.create_user(username='panel-delivery-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        order = FoodOrder.objects.create(
            user=self.user,
            quantity=1,
            total_price=60,
            delivery_date=timezone.localdate(),
            status='confirmed',
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse('staff_orders'))
        self.assertContains(response, 'Mark as Delivered')
        response = self.client.post(reverse('confirm_order_delivery', args=[order.id]), {
            'return_to': 'staff_orders',
        })

        self.assertRedirects(response, reverse('staff_orders'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')
        self.assertTrue(OrderNotification.objects.filter(
            recipient=self.user,
            order=order,
            message__contains='has been delivered',
        ).exists())
        self.assertNotContains(self.client.get(reverse('staff_orders')), 'Mark as Delivered')

    def test_customer_cannot_confirm_delivery_and_staff_cannot_deliver_cancelled_order(self):
        other_user = User.objects.create_user(username='another-customer', password='TestPass123')
        staff_user = User.objects.create_user(username='ready-staff', password='TestPass123')
        UserProfile.objects.create(user=staff_user, user_type='canteen_staff')
        ready_order = FoodOrder.objects.create(
            user=other_user, quantity=1, total_price=60,
            delivery_date=timezone.localdate(), status='ready',
        )
        cancelled_order = FoodOrder.objects.create(
            user=self.user,
            quantity=1,
            total_price=60,
            delivery_date=timezone.localdate(),
            status='cancelled',
        )
        self.client.force_login(self.user)

        self.assertEqual(
            self.client.post(reverse('confirm_order_delivery', args=[ready_order.id])).status_code,
            302,
        )
        self.client.force_login(staff_user)
        self.assertRedirects(
            self.client.post(reverse('confirm_order_delivery', args=[cancelled_order.id])),
            reverse('notifications'),
        )
        ready_order.refresh_from_db()
        cancelled_order.refresh_from_db()
        self.assertEqual(ready_order.status, 'ready')
        self.assertEqual(cancelled_order.status, 'cancelled')

    def test_order_history_handles_offer_order_without_menu_item(self):
        offer = FoodOffer.objects.create(
            name='Fresh Offer', description='Discounted meal', original_price=100,
            discounted_price=70, discount_percentage=30, available_quantity=5,
            offer_date=timezone.localdate(), created_by=self.user,
        )
        FoodOrder.objects.create(
            user=self.user, offer=offer, quantity=1, total_price=70,
            delivery_date=timezone.localdate(),
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('my_orders'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fresh Offer')

    def test_authenticated_customer_can_open_order_form(self):
        item = MenuItem.objects.create(
            name='Orderable Meal', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=60, available_quantity=10,
            date=timezone.localdate(), availability_status='available',
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('place_order_for_menu', args=[item.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Orderable Meal')

    def test_feedback_requires_login(self):
        response = self.client.get(reverse('submit_feedback'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_authenticated_customer_can_submit_feedback(self):
        item = MenuItem.objects.create(
            name='Feedback Meal', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=60, available_quantity=10,
            date=timezone.localdate(), availability_status='available',
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse('submit_feedback'), {
            'menu_item': item.id,
            'offer': '',
            'rating': 5,
            'comment': 'Very tasty meal',
        })
        self.assertRedirects(response, reverse('submit_feedback'))
        self.assertTrue(UserFeedback.objects.filter(user=self.user, menu_item=item).exists())

    def test_existing_admin_can_create_a_new_admin_account(self):
        admin_user = User.objects.create_user(username='admin1', password='StrongPass1!', first_name='Main', last_name='Admin')
        UserProfile.objects.update_or_create(user=admin_user, defaults={'user_type': 'admin'})
        admin_user.is_staff = True
        admin_user.is_superuser = False
        admin_user.save()

        self.client.force_login(admin_user)
        response = self.client.post(reverse('add_admin'), {
            'first_name': 'New',
            'last_name': 'Admin',
            'email': 'newadmin@example.com',
            'username': 'newadmin',
            'password1': 'StrongPass1!',
            'password2': 'StrongPass1!',
        })

        self.assertEqual(response.status_code, 302)
        new_admin = User.objects.get(username='newadmin')
        self.assertTrue(new_admin.is_staff)
        self.assertEqual(new_admin.profile.user_type, 'admin')
        self.assertRedirects(response, reverse('admin_add_admin'))

    def test_superuser_login_sets_admin_profile(self):
        user = User.objects.create_user(
            username='superadmin',
            password='StrongPass1!',
            first_name='Super',
            last_name='Admin',
            is_staff=True,
            is_superuser=True,
        )

        response = self.client.post(reverse('login'), {
            'username': 'superadmin',
            'password': 'StrongPass1!',
        })

        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertEqual(user.profile.user_type, 'admin')

    def test_admin_users_page_is_accessible_to_admin(self):
        admin_user = User.objects.create_user(
            username='adminlist',
            password='StrongPass1!',
            first_name='Admin',
            last_name='List',
            is_staff=True,
            is_superuser=True,
        )
        UserProfile.objects.update_or_create(user=admin_user, defaults={'user_type': 'admin'})

        self.client.force_login(admin_user)
        response = self.client.get(reverse('admin_users'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'All Registered Users')

    def test_admin_users_page_shows_admission_number(self):
        admin_user = User.objects.create_user(
            username='adminadmissions',
            password='StrongPass1!',
            is_staff=True,
            is_superuser=True,
        )
        UserProfile.objects.update_or_create(user=admin_user, defaults={'user_type': 'admin'})
        student = User.objects.create_user(username='studentadmission', password='StrongPass1!')
        UserProfile.objects.create(user=student, admission_id='STU-2026-42')

        self.client.force_login(admin_user)
        response = self.client.get(reverse('admin_users'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admission Number')
        self.assertContains(response, 'STU-2026-42')
        self.assertContains(response, 'Not provided')

    def test_admin_can_update_a_user_from_admin_page(self):
        admin_user = User.objects.create_user(
            username='adminupdate',
            password='StrongPass1!',
            first_name='Admin',
            last_name='Update',
            is_staff=True,
            is_superuser=True,
        )
        UserProfile.objects.update_or_create(user=admin_user, defaults={'user_type': 'admin'})
        target_user = User.objects.create_user(
            username='targetuser',
            password='StrongPass1!',
            first_name='Old',
            last_name='Name',
            email='old@example.com',
        )
        UserProfile.objects.update_or_create(user=target_user, defaults={'user_type': 'student'})

        self.client.force_login(admin_user)
        response = self.client.post(reverse('admin_user_edit', args=[target_user.id]), {
            'first_name': 'Updated',
            'last_name': 'User',
            'email': 'updated@example.com',
            'user_type': 'canteen_staff',
        })

        self.assertEqual(response.status_code, 302)
        target_user.refresh_from_db()
        self.assertEqual(target_user.first_name, 'Updated')
        self.assertEqual(target_user.profile.user_type, 'canteen_staff')

    def test_admin_can_delete_a_user_from_admin_page(self):
        admin_user = User.objects.create_user(
            username='admindelete',
            password='StrongPass1!',
            first_name='Admin',
            last_name='Delete',
            is_staff=True,
            is_superuser=True,
        )
        UserProfile.objects.update_or_create(user=admin_user, defaults={'user_type': 'admin'})
        target_user = User.objects.create_user(
            username='deleteuser',
            password='StrongPass1!',
            first_name='Delete',
            last_name='Me',
        )
        UserProfile.objects.update_or_create(user=target_user, defaults={'user_type': 'student'})

        self.client.force_login(admin_user)
        response = self.client.post(reverse('admin_user_delete', args=[target_user.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(username='deleteuser').exists())

    def test_staff_dashboard_shows_average_rating_from_feedback(self):
        staff_user = User.objects.create_user(
            username='staffrating',
            password='StrongPass1!',
            first_name='Staff',
            last_name='Member',
        )
        UserProfile.objects.update_or_create(user=staff_user, defaults={'user_type': 'canteen_staff'})
        menu_item = MenuItem.objects.create(
            name='Average Rating Meal', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=60, available_quantity=10,
            date=timezone.localdate(), availability_status='available',
        )
        UserFeedback.objects.create(user=self.user, menu_item=menu_item, rating=4, comment='Good')
        UserFeedback.objects.create(user=self.user, menu_item=menu_item, rating=5, comment='Excellent')

        self.client.force_login(staff_user)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '4.5')

    def test_admin_dashboard_links_to_all_orders_and_feedback_pages(self):
        admin_user = User.objects.create_user(
            username='admingateway',
            password='StrongPass1!',
            first_name='Admin',
            last_name='Gateway',
            is_staff=True,
            is_superuser=True,
        )
        UserProfile.objects.update_or_create(user=admin_user, defaults={'user_type': 'admin'})

        self.client.force_login(admin_user)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('staff_orders'))
        self.assertContains(response, reverse('staff_feedback'))

    def test_admin_dashboard_links_to_system_health_page(self):
        admin_user = User.objects.create_user(
            username='adminhealth',
            password='StrongPass1!',
            first_name='Admin',
            last_name='Health',
            is_staff=True,
            is_superuser=True,
        )
        UserProfile.objects.update_or_create(user=admin_user, defaults={'user_type': 'admin'})

        self.client.force_login(admin_user)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('admin_system_health'))

        health_response = self.client.get(reverse('admin_system_health'))
        self.assertEqual(health_response.status_code, 200)
        self.assertContains(health_response, 'System Health')

    def test_change_password_page_exposes_profile_context_and_updates_password(self):
        self.client.force_login(self.user)
        UserProfile.objects.update_or_create(user=self.user, defaults={'user_type': 'student'})

        get_response = self.client.get(reverse('change_password'))
        self.assertEqual(get_response.status_code, 200)
        self.assertIn('profile', get_response.context)

        response = self.client.post(reverse('change_password'), {
            'old_password': 'TestPass123',
            'new_password1': 'NewStrongPass1!',
            'new_password2': 'NewStrongPass1!',
        })

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewStrongPass1!'))

    def test_logged_in_dashboard_shows_uploaded_menu_and_order_link(self):
        MenuItem.objects.create(
            name='Dashboard Special', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=65, available_quantity=12,
            date=timezone.localdate() + timezone.timedelta(days=1),
            availability_status='available',
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Dashboard Special')
        self.assertContains(response, reverse('place_order_for_menu', args=[1]))
        self.assertNotContains(response, 'Coming Soon')

    def test_dashboard_shows_number_of_bookings(self):
        FoodOrder.objects.create(
            user=self.user, quantity=3, total_price=180,
            delivery_date=timezone.localdate(),
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, '<p class="stat-number">1</p>', html=True)

    def test_login_shows_password_error_for_wrong_password(self):
        response = self.client.post(reverse('login'), {
            'username': 'customer',
            'password': 'WrongPass1!',
        })
        self.assertContains(response, 'Password error')
        self.assertNotContains(response, 'Invalid username or password.')

    def test_login_page_has_show_password_toggle(self):
        response = self.client.get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="show-password-toggle"')
        self.assertContains(response, 'Show password')
        self.assertContains(response, 'type="password"')
        self.assertContains(response, "passwordInput.type = this.checked ? 'text' : 'password'")

    def test_canteen_staff_dashboard_set_prices_link_goes_to_menu_management(self):
        UserProfile.objects.create(user=self.user, user_type='canteen_staff')
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'href="/staff/menu/"')
        self.assertContains(response, 'Set Prices')

    def test_canteen_staff_dashboard_manage_quantity_link_goes_to_menu_management(self):
        UserProfile.objects.create(user=self.user, user_type='canteen_staff')
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, '<a href="/staff/menu/" class="btn btn-primary">Manage Quantity</a>')

    def test_homepage_shows_only_current_uploaded_items(self):
        today = timezone.localdate()
        MenuItem.objects.create(
            name='Staff Special', description='Fresh meal', category='Lunch',
            meal_type='lunch', price=75, available_quantity=10, date=today,
            availability_status='available',
        )
        MenuItem.objects.create(
            name='Tomorrow Special', description='Future meal', category='Lunch',
            meal_type='lunch', price=80, available_quantity=10,
            date=today + timezone.timedelta(days=1), availability_status='available',
        )
        FoodOffer.objects.create(
            name='Staff Offer', description='Discounted meal', original_price=100,
            discounted_price=70, discount_percentage=30, available_quantity=5,
            offer_date=today, created_by=self.user,
        )

        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Staff Special')
        self.assertContains(response, 'Staff Offer')
        self.assertContains(response, 'Tomorrow Special')
        self.assertNotContains(response, 'Paneer Paratha')

    def test_homepage_feature_buttons_redirect_guests_to_login(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'href="/login/"')
        self.assertContains(response, 'Explore Menu')
        self.assertContains(response, 'View Offers')

    @patch('canteen.views.menu_update_window_open', return_value=True)
    def test_canteen_staff_can_update_uploaded_menu_item(self, update_window):
        UserProfile.objects.create(user=self.user, user_type='canteen_staff')
        item = MenuItem.objects.create(
            name='Original Name', description='Meal', category='Lunch',
            meal_type='lunch', price=50, available_quantity=10,
            date=timezone.localdate(), availability_status='available',
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse('staff_menu_edit', args=[item.id]), {
            'name': 'Updated Name', 'description': 'Updated meal', 'category': 'Lunch',
            'meal_type': 'lunch', 'price': '60.00', 'available_quantity': 8,
            'date': timezone.localdate(), 'opening_time': f'{timezone.localdate()}T08:00',
            'closing_time': f'{timezone.localdate()}T20:00',
            'auto_approve_orders': 'on', 'availability_status': 'available',
        })
        self.assertRedirects(response, reverse('staff_menu'))
        item.refresh_from_db()
        self.assertEqual(item.name, 'Updated Name')
