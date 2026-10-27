from io import BytesIO
from datetime import timedelta
from decimal import Decimal
from xml.sax.saxutils import escape
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.views.decorators.http import require_http_methods, require_POST
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from .forms import (
    CustomUserCreationForm,
    AdminCreationForm,
    CustomAuthenticationForm,
    UserProfileForm,
    ChangePasswordForm,
    MenuItemForm,
    FoodOfferForm,
    FoodOrderForm,
    OrderStatusForm,
    UserFeedbackForm,
)
from .models import (
    UserProfile,
    ActivityLog,
    AdminSession,
    CanteenStaffSession,
    MenuItem,
    FoodOffer,
    FoodOrder,
    OrderNotification,
    UserFeedback,
)


def get_client_ip(request):
    """Get client IP address from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def get_order_item_name(order):
    if order.menu_item:
        return order.menu_item.name
    if order.offer:
        return order.offer.name
    return 'food item'


def parse_month_value(month_value):
    if len(month_value) != 7:
        return None
    try:
        month_start = parse_date(f'{month_value}-01')
    except ValueError:
        return None
    if month_start is None or month_start.strftime('%Y-%m') != month_value:
        return None
    return month_start


def notify_order_placed(order):
    item_name = get_order_item_name(order)
    user_name = order.user.get_full_name() or order.user.username
    profile, _ = UserProfile.objects.get_or_create(user=order.user)
    staff_message = (
        f'New order #{order.id} placed by {user_name} '
        f'(Admission number: {profile.admission_id or "Not provided"}) ordered '
        f'{order.quantity} x {item_name}.'
    )
    staff_users = User.objects.filter(profile__user_type='canteen_staff')
    OrderNotification.objects.bulk_create(
        OrderNotification(recipient=staff_user, order=order, message=staff_message)
        for staff_user in staff_users
    )


def notify_order_confirmed(order):
    OrderNotification.objects.create(
        recipient=order.user,
        order=order,
        message=f'Your order #{order.id} for {get_order_item_name(order)} has been confirmed.',
    )


def notify_order_delivered(order):
    OrderNotification.objects.create(
        recipient=order.user,
        order=order,
        message=f'Your order #{order.id} for {get_order_item_name(order)} has been delivered.',
    )


def is_admin_user(user):
    """Return True for admin users regardless of whether the profile was created manually or by Django."""
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        profile = None

    return bool(user.is_superuser or user.is_staff or (profile and profile.user_type == 'admin'))


def menu_update_window_open(now=None):
    """Menu updates are allowed anytime for staff; the date and time fields control ordering availability."""
    return True


def is_menu_ordering_open(menu_item, now=None):
    """Order is allowed between the configured opening and closing datetimes."""
    if menu_item is None:
        return False
    current = timezone.localtime(now) if now else timezone.localtime()
    current_datetime = current
    if not (menu_item.opening_time and menu_item.closing_time):
        return True

    opening_datetime = menu_item.opening_time
    closing_datetime = menu_item.closing_time
    if timezone.is_naive(opening_datetime):
        opening_datetime = timezone.make_aware(opening_datetime)
    if timezone.is_naive(closing_datetime):
        closing_datetime = timezone.make_aware(closing_datetime)

    return opening_datetime <= current_datetime < closing_datetime


def can_cancel_order(order, now=None):
    """Return whether the customer can cancel before the item's booking cutoff."""
    if order.status in ['cancelled', 'delivered']:
        return False

    if order.menu_item:
        deadline = order.menu_item.closing_time
    elif order.offer:
        deadline = order.offer.end_time
    else:
        return False

    if timezone.is_naive(deadline):
        deadline = timezone.make_aware(deadline)
    current = timezone.now() if now is None else now
    if timezone.is_naive(current):
        current = timezone.make_aware(current)
    return current < deadline


def home(request):
    """Public homepage with the canteen's currently uploaded menu and offers."""
    today = timezone.localdate()
    menu_items = MenuItem.objects.filter(
        date__gte=today,
        availability_status='available',
    ).order_by('date', 'meal_type', 'name')
    offers = FoodOffer.objects.filter(
        offer_date=today,
        status='active',
        available_quantity__gt=0,
    ).order_by('start_time', 'name')
    return render(request, 'canteen/home.html', {
        'menu_items': menu_items,
        'offers': offers,
    })


@require_http_methods(["GET", "POST"])
def register(request):
    """User registration view"""
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            try:
                user = form.save()
                messages.success(request, f'Welcome {user.first_name}! Your account has been created successfully. Please log in.')
                return redirect('login')
            except IntegrityError as e:
                # Handle race condition or duplicate username
                if 'username' in str(e):
                    messages.error(request, 'This username is already taken. Please choose another.')
                elif 'email' in str(e):
                    messages.error(request, 'This email address is already registered.')
                else:
                    messages.error(request, 'An error occurred during registration. Please try again.')
        else:
            # Add form errors to messages
            for field, errors in list(form.errors.items()):
                for error in errors:
                    messages.error(request, f'{error}')
    else:
        form = CustomUserCreationForm()
    
    context = {'form': form}
    return render(request, 'canteen/register.html', context)


@require_http_methods(["GET", "POST"])
def user_login(request):
    """User login view with admin/staff session management"""
    if request.method == 'POST':
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')

            user = authenticate(username=username, password=password)
            if user is not None:
                try:
                    profile = user.profile
                except UserProfile.DoesNotExist:
                    profile = UserProfile.objects.create(user=user)

                if user.is_staff or user.is_superuser:
                    profile.user_type = 'admin'
                    profile.save(update_fields=['user_type'])

                if profile.user_type == 'admin':
                    if not (user.is_staff or user.is_superuser):
                        messages.error(request, 'Admin accounts are managed securely and cannot be created from the public registration form.')
                        return redirect('login')
                    active_other_admin = AdminSession.objects.filter(is_active=True).exclude(admin_user=user).exists()
                    if active_other_admin:
                        messages.error(request, 'Not allowed. Another admin is already logged in.')
                        return redirect('login')
                    AdminSession.objects.update_or_create(
                        admin_user=user,
                        defaults={'is_active': True}
                    )

                elif profile.user_type == 'canteen_staff':
                    active_other_staff = CanteenStaffSession.objects.filter(is_active=True).exclude(staff_user=user).exists()
                    if active_other_staff:
                        messages.error(request, 'Not allowed. Another canteen staff member is already logged in.')
                        return redirect('login')
                    CanteenStaffSession.objects.update_or_create(
                        staff_user=user,
                        defaults={'is_active': True}
                    )

                ActivityLog.objects.create(
                    user=user,
                    action='login',
                    description=f'{profile.get_user_type_display()} logged in',
                    ip_address=get_client_ip(request)
                )

                login(request, user)
                messages.success(request, f'Welcome back, {user.first_name}!')
                return redirect('dashboard')
            else:
                messages.error(request, 'Invalid username or password.')
        else:
            for field, errors in list(form.errors.items()):
                for error in errors:
                    if field == '__all__':
                        form.add_error('password', 'Password error')
                    else:
                        messages.error(request, str(error))
    else:
        form = CustomAuthenticationForm()

    context = {'form': form}
    return render(request, 'canteen/login.html', context)


@login_required(login_url='login')
def user_logout(request):
    """User logout view with session cleanup"""
    try:
        profile = request.user.profile
        
        # Clear admin/staff sessions on logout
        if profile.user_type == 'admin':
            AdminSession.objects.filter(admin_user=request.user).update(is_active=False)
        elif profile.user_type == 'canteen_staff':
            CanteenStaffSession.objects.filter(staff_user=request.user).update(is_active=False)
        
        # Log the activity
        ActivityLog.objects.create(
            user=request.user,
            action='logout',
            description=f'{profile.get_user_type_display()} logged out',
            ip_address=get_client_ip(request)
        )
    except UserProfile.DoesNotExist:
        pass
    
    logout(request)
    messages.success(request, 'You have been logged out successfully.')
    return redirect('home')


@login_required(login_url='login')
def dashboard(request):
    """Role-based dashboard routing"""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)
    
    # Route to appropriate dashboard based on user type
    if is_admin_user(request.user):
        return admin_dashboard(request)
    elif profile.user_type == 'canteen_staff':
        return canteen_staff_dashboard(request)
    else:
        return user_dashboard(request)


@login_required(login_url='login')
def menu_catalog(request):
    """Authenticated users can view the menu and order food."""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)

    today = timezone.localdate()
    menu_items = MenuItem.objects.filter(date=today, availability_status='available').order_by('meal_type')
    offers = FoodOffer.objects.filter(offer_date=today, status='active').order_by('start_time')
    context = {
        'user': request.user,
        'profile': profile,
        'menu_items': menu_items,
        'offers': offers,
    }
    return render(request, 'canteen/customer_menu.html', context)


@login_required(login_url='login')
def place_order(request, menu_id=None):
    """Place a food order for an authenticated user."""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)

    initial_menu = None
    if menu_id:
        initial_menu = MenuItem.objects.filter(pk=menu_id).first()

    if request.method == 'POST':
        form = FoodOrderForm(request.POST)
        if form.is_valid():
            menu_item = form.cleaned_data['menu_item']
            quantity = form.cleaned_data['quantity']

            if menu_item.available_quantity <= 0:
                messages.error(request, f'{menu_item.name} is currently out of stock.')
                return redirect('menu_catalog')

            if not is_menu_ordering_open(menu_item):
                messages.error(request, f'Food ordering for {menu_item.name} is available from {timezone.localtime(menu_item.opening_time).strftime("%b %d, %Y %I:%M %p")} until {timezone.localtime(menu_item.closing_time).strftime("%b %d, %Y %I:%M %p")}.')
                return redirect('menu_catalog')

            if quantity > menu_item.available_quantity:
                messages.error(request, f'Only {menu_item.available_quantity} units are available for {menu_item.name}.')
                return redirect('menu_catalog')

            order = form.save(commit=False)
            order.user = request.user
            order.total_price = Decimal(str(menu_item.price)) * quantity
            order.status = 'confirmed' if menu_item.auto_approve_orders else 'pending'
            order.save()
            notify_order_placed(order)
            if order.status == 'confirmed':
                notify_order_confirmed(order)
            menu_item.available_quantity = max(0, menu_item.available_quantity - quantity)
            menu_item.save(update_fields=['available_quantity'])
            ActivityLog.objects.create(
                user=request.user,
                action='order_placed',
                description=f'Placed order for {quantity} x {menu_item.name} ({order.status})',
                ip_address=get_client_ip(request),
            )
            messages.success(request, 'Your order was placed successfully.' if not menu_item.auto_approve_orders else 'Your order was approved automatically and placed successfully.')
            return redirect('my_orders')
    else:
        form = FoodOrderForm(initial={
            'menu_item': initial_menu,
            'delivery_date': timezone.localdate() + timezone.timedelta(days=1) if initial_menu else timezone.localdate(),
        })

    context = {
        'user': request.user,
        'profile': profile,
        'form': form,
        'menu_items': MenuItem.objects.filter(availability_status='available').order_by('meal_type'),
    }
    return render(request, 'canteen/place_order.html', context)


@login_required(login_url='login')
def my_orders(request):
    """Show all orders placed by the current user."""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)

    orders = FoodOrder.objects.filter(user=request.user).select_related('menu_item', 'offer').order_by('-created_at')
    for order in orders:
        order.can_cancel = can_cancel_order(order)
    context = {
        'user': request.user,
        'profile': profile,
        'orders': orders,
    }
    return render(request, 'canteen/my_orders.html', context)


@login_required(login_url='login')
@require_POST
def cancel_order(request, order_id):
    """Cancel the signed-in user's order before its booking cutoff and restore stock."""
    with transaction.atomic():
        order = get_object_or_404(
            FoodOrder.objects.select_for_update().select_related('menu_item', 'offer'),
            pk=order_id,
            user=request.user,
        )
        if not can_cancel_order(order):
            messages.error(request, 'This order can no longer be cancelled.')
            return redirect('my_orders')

        if order.menu_item_id:
            menu_item = get_object_or_404(
                MenuItem.objects.select_for_update(),
                pk=order.menu_item_id,
            )
            menu_item.available_quantity += order.quantity
            menu_item.save(update_fields=['available_quantity', 'updated_at'])
        elif order.offer_id:
            offer = get_object_or_404(
                FoodOffer.objects.select_for_update(),
                pk=order.offer_id,
            )
            offer.available_quantity += order.quantity
            offer.save(update_fields=['available_quantity', 'updated_at'])

        order.status = 'cancelled'
        order.save(update_fields=['status', 'updated_at'])
        ActivityLog.objects.create(
            user=request.user,
            action='order_cancelled',
            description=f'Cancelled order #{order.id}.',
            ip_address=get_client_ip(request),
        )

    messages.success(request, f'Order {order.id} was cancelled and its quantity returned to stock.')
    return redirect('my_orders')


def get_report_orders(request, period, include_all_users):
    if period == 'date':
        date_value = request.GET.get('date', timezone.localdate().isoformat())
        try:
            selected_period = parse_date(date_value)
        except ValueError:
            selected_period = None
        if selected_period is None:
            return HttpResponseBadRequest('Enter a valid date in YYYY-MM-DD format.')
        period_start = selected_period
        period_end = selected_period + timedelta(days=1)
        period_label = selected_period.strftime('%Y-%m-%d')
    elif period == 'month':
        month_value = request.GET.get('month', timezone.localdate().strftime('%Y-%m'))
        selected_period = parse_month_value(month_value)
        if selected_period is None:
            return HttpResponseBadRequest('Enter a valid month in YYYY-MM format.')
        period_start = selected_period
        period_end = (period_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        period_label = selected_period.strftime('%Y-%m')
    else:
        return HttpResponseBadRequest('Select a valid report period.')

    selected_status = request.GET.get('status', '')
    if selected_status and selected_status not in dict(FoodOrder.STATUS_CHOICES):
        return HttpResponseBadRequest('Select a valid order status.')

    orders = FoodOrder.objects.select_related(
        'user', 'user__profile', 'menu_item', 'offer',
    ).filter(
        delivery_date__gte=period_start,
        delivery_date__lt=period_end,
    )
    if not include_all_users:
        orders = orders.filter(user=request.user)
    if selected_status:
        orders = orders.filter(status=selected_status)

    return orders.order_by('created_at'), selected_period, selected_status, period_label


def get_report_order_details(order):
    user = order.user
    try:
        profile = user.profile
        admission_id = profile.admission_id
        user_type = profile.get_user_type_display()
    except UserProfile.DoesNotExist:
        admission_id = ''
        user_type = ''
    item_name = order.menu_item.name if order.menu_item else (
        order.offer.name if order.offer else 'Removed item'
    )
    item_type = 'Menu item' if order.menu_item_id else (
        'Offer' if order.offer_id else 'Removed item'
    )
    item_id = order.menu_item_id or order.offer_id
    full_name = user.get_full_name()
    return [
        order.id,
        timezone.localtime(order.created_at).strftime('%Y-%m-%d %H:%M:%S'),
        timezone.localtime(order.updated_at).strftime('%Y-%m-%d %H:%M:%S'),
        order.delivery_date.isoformat(),
        order.status,
        order.get_status_display(),
        item_name,
        item_type,
        item_id or '',
        order.quantity,
        str(order.total_price),
        order.notes,
        user.id,
        user.username,
        full_name,
        user.first_name,
        user.last_name,
        user.email,
        admission_id,
        user_type,
    ]


REPORT_COLUMNS = [
    'Order ID',
    'Order placed',
    'Order updated',
    'Delivery date',
    'Status code',
    'Status',
    'Item',
    'Item type',
    'Item ID',
    'Quantity',
    'Total price',
    'Order notes',
    'User ID',
    'Username',
    'Full name',
    'First name',
    'Last name',
    'Email',
    'Admission ID',
    'User type',
]


def create_excel_order_report(orders, period_label, selected_status):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = 'Orders'
    worksheet.append([f'Order report - {period_label}'])
    worksheet.append([f'Status filter: {selected_status or "All statuses"}'])
    worksheet.append(REPORT_COLUMNS)
    for cell in worksheet[3]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='1F5A44')
    for order in orders:
        worksheet.append(get_report_order_details(order))
        for cell in worksheet[worksheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = 's'
    worksheet.freeze_panes = 'A4'
    worksheet.auto_filter.ref = f'A3:T{worksheet.max_row}'
    for column, width in {
        'A': 12, 'B': 21, 'C': 21, 'D': 15, 'E': 15, 'F': 20,
        'G': 28, 'H': 16, 'I': 12, 'J': 10, 'K': 14, 'L': 36,
        'M': 12, 'N': 20, 'O': 28, 'P': 20, 'Q': 20, 'R': 32,
        'S': 18, 'T': 22,
    }.items():
        worksheet.column_dimensions[column].width = width
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def create_pdf_order_report(orders, period_label, selected_status):
    output = BytesIO()
    report_title = 'Order History Report'
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=22 * mm,
        bottomMargin=18 * mm,
        title=f'{report_title} - {period_label}',
        author='Campus Food Management',
    )
    styles = getSampleStyleSheet()
    navy = colors.HexColor('#173B5E')
    orange = colors.HexColor('#E87524')
    muted = colors.HexColor('#697586')
    pale_blue = colors.HexColor('#F2F6FA')
    pale_orange = colors.HexColor('#FFF7F1')
    border = colors.HexColor('#D7DEE7')
    styles.add(ParagraphStyle(
        name='OrderReportTitle',
        parent=styles['Title'],
        fontName='Helvetica-Bold',
        fontSize=17,
        leading=20,
        textColor=navy,
        alignment=2,
        spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name='OrderReportSubTitle',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        textColor=muted,
        alignment=2,
    ))
    styles.add(ParagraphStyle(
        name='OrderReportSection',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=navy,
        spaceBefore=3 * mm,
        spaceAfter=2 * mm,
    ))
    styles.add(ParagraphStyle(
        name='OrderReportCardLabel',
        parent=styles['Normal'],
        fontSize=6.5,
        leading=8,
        textColor=muted,
        uppercase=True,
    ))
    styles.add(ParagraphStyle(
        name='OrderReportCardValue',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=12,
        textColor=navy,
        wordWrap='CJK',
    ))
    styles.add(ParagraphStyle(
        name='OrderReportBody',
        parent=styles['BodyText'],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#273444'),
    ))
    styles.add(ParagraphStyle(
        name='OrderReportFieldLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=7,
        leading=9,
        textColor=muted,
        wordWrap='CJK',
    ))
    styles.add(ParagraphStyle(
        name='OrderReportFieldValue',
        parent=styles['Normal'],
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor('#202B38'),
        wordWrap='CJK',
    ))
    styles.add(ParagraphStyle(
        name='OrderReportOrderHeading',
        parent=styles['Heading3'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        textColor=colors.white,
        spaceAfter=0,
    ))

    def draw_page_chrome(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(muted)
        canvas.drawString(doc.leftMargin, height - 10 * mm, timezone.localtime().strftime('%d/%m/%Y, %H:%M'))
        canvas.drawCentredString(width / 2, height - 10 * mm, report_title)
        canvas.setStrokeColor(border)
        canvas.setLineWidth(0.5)
        canvas.line(doc.leftMargin, height - 13 * mm, width - doc.rightMargin, height - 13 * mm)
        canvas.line(doc.leftMargin, 12 * mm, width - doc.rightMargin, 12 * mm)
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(muted)
        canvas.drawString(doc.leftMargin, 8 * mm, 'Campus Food Management | Confidential order details')
        canvas.drawRightString(width - doc.rightMargin, 8 * mm, f'Page {doc.page}')
        canvas.restoreState()

    def section_heading(number, text):
        heading = Table(
            [[Paragraph(f'{number}. {escape(text)}', styles['OrderReportSection'])]],
            colWidths=[180 * mm],
        )
        heading.setStyle(TableStyle([
            ('LINEBELOW', (0, 0), (-1, -1), 0.7, border),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        return heading

    def information_card(label, value):
        card = Table(
            [[
                Paragraph(escape(label.upper()), styles['OrderReportCardLabel']),
                Paragraph(
                    escape(str(value if value is not None else 'Not provided')),
                    styles['OrderReportCardValue'],
                ),
            ]],
            colWidths=[31 * mm, 29 * mm],
        )
        card.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), pale_blue),
            ('BOX', (0, 0), (-1, -1), 0.5, border),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 7),
            ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        return card

    def compact_status_card(label, value):
        card = Table(
            [
                [Paragraph(escape(label.upper()), styles['OrderReportCardLabel'])],
                [Paragraph(str(value), styles['OrderReportCardValue'])],
            ],
            colWidths=[36 * mm],
        )
        card.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), pale_blue),
            ('BOX', (0, 0), (-1, -1), 0.5, border),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        return card

    summary = orders.aggregate(
        order_count=Count('pk'),
        quantity_total=Sum('quantity'),
        revenue=Sum('total_price'),
        customer_count=Count('user', distinct=True),
    )
    status_counts = {
        row['status']: row['total']
        for row in orders.values('status').annotate(total=Count('pk'))
    }
    order_count = summary['order_count'] or 0
    total_quantity = summary['quantity_total'] or 0
    total_amount = summary['revenue'] or Decimal('0.00')
    customer_count = summary['customer_count'] or 0
    report_type = 'Monthly order report' if len(period_label) == 7 else 'Daily order report'

    story = [
        Table(
            [[
                Table([
                    [Paragraph('CAMPUS FOOD MANAGEMENT', ParagraphStyle(
                        'OrderReportBrand',
                        parent=styles['Heading2'],
                        fontName='Helvetica-Bold',
                        fontSize=12,
                        leading=14,
                        textColor=navy,
                        spaceAfter=2,
                    ))],
                    [Paragraph('Canteen Operations', styles['OrderReportBody'])],
                ], colWidths=[90 * mm]),
                Table([
                    [Paragraph(report_type.upper(), styles['OrderReportTitle'])],
                    [Paragraph(f'Generated: {timezone.localtime().strftime("%d/%m/%Y")}', styles['OrderReportSubTitle'])],
                ], colWidths=[90 * mm]),
            ]],
            colWidths=[90 * mm, 90 * mm],
        ),
        Spacer(1, 3 * mm),
        Table([['']], colWidths=[180 * mm], rowHeights=[1.2 * mm], style=TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), navy),
            ('LINEBEFORE', (0, 0), (0, 0), 3, orange),
        ])),
        section_heading(1, 'Report Information'),
        Spacer(1, 2 * mm),
    ]

    info_cards = [
        information_card('Report period', period_label),
        information_card('Order status', dict(FoodOrder.STATUS_CHOICES).get(selected_status, 'All statuses')),
        information_card('Customers in report', customer_count),
    ]
    story.append(Table([info_cards], colWidths=[60 * mm, 60 * mm, 60 * mm], style=TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ])))
    story.extend([
        section_heading(2, 'Executive Order Summary'),
        Spacer(1, 2 * mm),
    ])
    summary_cards = [
        information_card('Total orders', order_count),
        information_card('Items ordered', total_quantity),
        information_card('Total value (INR)', f'{total_amount:,.2f}'),
    ]
    story.append(Table([summary_cards], colWidths=[60 * mm, 60 * mm, 60 * mm], style=TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ])))
    if customer_count > 0:
        story.append(Spacer(1, 1 * mm))
        story.append(Paragraph(
            f'The selected period contains {order_count} order(s) from {customer_count} customer(s), '
            f'covering {total_quantity} item(s).',
            styles['OrderReportBody'],
        ))
    story.extend([
        section_heading(3, 'Order Status Breakdown'),
        Spacer(1, 2 * mm),
    ])
    status_cells = []
    for status_code, status_label in FoodOrder.STATUS_CHOICES:
        status_cells.append(compact_status_card(status_label, status_counts.get(status_code, 0)))
    status_table = Table(
        [status_cells],
        colWidths=[36 * mm] * len(status_cells),
    )
    status_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(status_table)
    story.extend([
        section_heading(4, 'Order and Customer Details'),
        Spacer(1, 2 * mm),
    ])

    order_fields = [
        ('Order ID', 0),
        ('Status', 5),
        ('Order placed', 1),
        ('Last updated', 2),
        ('Delivery date', 3),
        ('Item', 6),
        ('Status code', 4),
        ('Item type', 7),
        ('Item ID', 8),
        ('Quantity', 9),
        ('Total price (INR)', 10),
        ('Customer ID', 12),
        ('Username', 13),
        ('Customer name', 14),
        ('First name', 15),
        ('Last name', 16),
        ('Email', 17),
        ('Admission ID', 18),
        ('User type', 19),
    ]

    def field_value(value):
        if value is None or value == '':
            value = 'Not provided'
        return Paragraph(escape(str(value)), styles['OrderReportFieldValue'])

    card_widths = [25 * mm, 65 * mm, 25 * mm, 65 * mm]
    card_style = TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), pale_orange),
        ('BACKGROUND', (2, 0), (2, -1), pale_orange),
        ('LINEBEFORE', (0, 0), (0, -1), 3, orange),
        ('GRID', (0, 0), (-1, -1), 0.35, border),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ])
    for order in orders:
        details = get_report_order_details(order)
        detail_rows = []
        for index in range(0, len(order_fields), 2):
            first_label, first_detail_index = order_fields[index]
            second_field = order_fields[index + 1] if index + 1 < len(order_fields) else None
            detail_rows.append([
                Paragraph(escape(first_label), styles['OrderReportFieldLabel']),
                field_value(details[first_detail_index]),
                Paragraph(escape(second_field[0]), styles['OrderReportFieldLabel']) if second_field else '',
                field_value(details[second_field[1]]) if second_field else '',
            ])
        detail_table = Table(detail_rows, colWidths=card_widths, splitByRow=1)
        detail_table.setStyle(card_style)
        heading = Table(
            [[Paragraph(f'ORDER #{order.id}', styles['OrderReportOrderHeading'])]],
            colWidths=[180 * mm],
        )
        heading.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), navy),
            ('LINEBEFORE', (0, 0), (0, -1), 3, orange),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(KeepTogether([heading, detail_table]))
        notes = order.notes.strip()
        if notes:
            story.append(Spacer(1, 1 * mm))
            notes_table = Table(
                [[
                    Paragraph('ORDER NOTES', styles['OrderReportFieldLabel']),
                    Paragraph(escape(notes).replace('\n', '<br/>'), styles['OrderReportFieldValue']),
                ]],
                colWidths=[25 * mm, 155 * mm],
            )
            notes_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), pale_orange),
                ('LINEBEFORE', (0, 0), (0, -1), 3, orange),
                ('BOX', (0, 0), (-1, -1), 0.35, border),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.append(notes_table)
        story.append(Spacer(1, 4 * mm))

    if order_count == 0:
        story.append(Paragraph(
            'No orders match the selected report period and status filter.',
            styles['OrderReportBody'],
        ))

    document.build(story, onFirstPage=draw_page_chrome, onLaterPages=draw_page_chrome)
    return output.getvalue()


def download_order_report(request, period, report_format, include_all_users):
    report_format = report_format.lower()
    if report_format not in ('xlsx', 'pdf'):
        return HttpResponseBadRequest('Select PDF or Excel as the report format.')

    result = get_report_orders(request, period, include_all_users)
    if isinstance(result, HttpResponseBadRequest):
        return result
    orders, _, selected_status, period_label = result
    if report_format == 'xlsx':
        content = create_excel_order_report(orders, period_label, selected_status)
        content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else:
        content = create_pdf_order_report(orders, period_label, selected_status)
        content_type = 'application/pdf'

    response = HttpResponse(content, content_type=content_type)
    response['Content-Disposition'] = (
        f'attachment; filename="orders_{period_label}.{report_format}"'
    )
    return response


@login_required(login_url='login')
def download_my_order_report(request, period, report_format):
    return download_order_report(
        request,
        period,
        report_format,
        include_all_users=False,
    )


@login_required(login_url='login')
def download_staff_order_report(request, period, report_format):
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = None
    if not (
        (profile and profile.user_type in ['canteen_staff', 'admin'])
        or request.user.is_staff
        or request.user.is_superuser
    ):
        messages.error(request, 'Access denied. This section is only for staff and admin.')
        return redirect('dashboard')
    return download_order_report(
        request,
        period,
        report_format,
        include_all_users=True,
    )


@login_required(login_url='login')
def my_orders_by_date(request):
    """Show only the signed-in user's orders for a selected delivery date."""
    date_value = request.GET.get('date')
    selected_date = parse_date(date_value) if date_value else timezone.localdate()
    if selected_date is None:
        return HttpResponseBadRequest('Enter a valid date in YYYY-MM-DD format.')

    selected_status = request.GET.get('status', '')
    valid_statuses = dict(FoodOrder.STATUS_CHOICES)
    if selected_status and selected_status not in valid_statuses:
        return HttpResponseBadRequest('Select a valid order status.')

    orders = FoodOrder.objects.filter(
        user=request.user,
        delivery_date=selected_date,
    ).select_related('menu_item', 'offer')
    if selected_status:
        orders = orders.filter(status=selected_status)
    orders = orders.order_by('-created_at')
    for order in orders:
        order.can_cancel = can_cancel_order(order)
    return render(request, 'canteen/my_orders_by_date.html', {
        'orders': orders,
        'selected_date': selected_date,
        'selected_status': selected_status,
        'status_choices': FoodOrder.STATUS_CHOICES,
    })


@login_required(login_url='login')
def my_orders_by_month(request):
    """Show only the signed-in user's orders for a selected delivery month."""
    month_value = request.GET.get('month', timezone.localdate().strftime('%Y-%m'))
    month_start = parse_month_value(month_value)
    if month_start is None:
        return HttpResponseBadRequest('Enter a valid month in YYYY-MM format.')
    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)

    selected_status = request.GET.get('status', '')
    valid_statuses = dict(FoodOrder.STATUS_CHOICES)
    if selected_status and selected_status not in valid_statuses:
        return HttpResponseBadRequest('Select a valid order status.')

    orders = FoodOrder.objects.filter(
        user=request.user,
        delivery_date__gte=month_start,
        delivery_date__lt=month_end,
    ).select_related('menu_item', 'offer')
    if selected_status:
        orders = orders.filter(status=selected_status)
    orders = orders.order_by('-created_at')
    for order in orders:
        order.can_cancel = can_cancel_order(order)
    return render(request, 'canteen/my_orders_by_month.html', {
        'orders': orders,
        'selected_month': month_start,
        'selected_status': selected_status,
        'status_choices': FoodOrder.STATUS_CHOICES,
    })


@login_required(login_url='login')
def notifications(request):
    """Show order notifications for the signed-in account."""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)

    user_notifications = OrderNotification.objects.filter(
        recipient=request.user,
    ).select_related('order')
    return render(request, 'canteen/notifications.html', {
        'user': request.user,
        'profile': profile,
        'notifications': user_notifications,
    })


@login_required(login_url='login')
@require_POST
def confirm_order_delivery(request, order_id):
    """Let canteen staff confirm handover of any order that is not cancelled."""
    redirect_view = 'staff_orders' if request.POST.get('return_to') == 'staff_orders' else 'notifications'
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if profile.user_type != 'canteen_staff' and not is_admin_user(request.user):
        messages.error(request, 'Only canteen staff can confirm order delivery.')
        return redirect('notifications')

    with transaction.atomic():
        order = get_object_or_404(FoodOrder.objects.select_for_update(), pk=order_id)
        if order.status in ['delivered', 'cancelled']:
            messages.error(request, 'Delivered or cancelled orders cannot be confirmed as delivered.')
            return redirect(redirect_view)

        order.status = 'delivered'
        order.save(update_fields=['status', 'updated_at'])
        notify_order_delivered(order)

    messages.success(request, f'Order {order.id} delivery confirmed.')
    return redirect(redirect_view)


def user_dashboard(request):
    """Regular user dashboard view"""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)
    
    menu_items = MenuItem.objects.filter(
        date__gte=timezone.localdate(),
        availability_status='available',
        available_quantity__gt=0,
    ).order_by('date', 'meal_type', 'name')[:6]
    offers = FoodOffer.objects.filter(
        offer_date__gte=timezone.localdate(),
        status='active',
        available_quantity__gt=0,
    ).order_by('offer_date', 'start_time')[:3]
    bookings_count = FoodOrder.objects.filter(user=request.user).count()
    notifications_count = OrderNotification.objects.filter(recipient=request.user).count()
    context = {
        'user': request.user,
        'profile': profile,
        'menu_items': menu_items,
        'offers': offers,
        'orders_count': bookings_count,
        'bookings_count': bookings_count,
        'notifications_count': notifications_count,
    }
    return render(request, 'canteen/dashboard.html', context)


def admin_dashboard(request):
    """Admin dashboard with activity, users, and feedback"""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)

    all_users = User.objects.count()
    total_orders = FoodOrder.objects.count()
    activity_logs = ActivityLog.objects.all()[:20]
    user_logins = ActivityLog.objects.filter(action='login').order_by('-timestamp')[:10]
    feedback_list = UserFeedback.objects.select_related('user', 'menu_item', 'offer').all()[:10]

    context = {
        'user': request.user,
        'profile': profile,
        'all_users': all_users,
        'total_orders': total_orders,
        'activity_logs': activity_logs,
        'user_logins': user_logins,
        'feedback_list': feedback_list,
    }
    return render(request, 'canteen/admin_dashboard.html', context)


@login_required(login_url='login')
def system_health(request):
    """Show the operational health of the campus canteen system to administrators."""
    if not is_admin_user(request.user):
        messages.error(request, 'Access denied. This section is only for administrators.')
        return redirect('dashboard')

    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)

    all_users = User.objects.count()
    total_orders = FoodOrder.objects.count()
    total_feedback = UserFeedback.objects.count()
    active_menu_items = MenuItem.objects.filter(availability_status='available').count()
    active_offers = FoodOffer.objects.filter(status='active').count()
    pending_orders = FoodOrder.objects.filter(status='pending').count()
    avg_rating = UserFeedback.objects.aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0

    context = {
        'user': request.user,
        'profile': profile,
        'all_users': all_users,
        'total_orders': total_orders,
        'total_feedback': total_feedback,
        'active_menu_items': active_menu_items,
        'active_offers': active_offers,
        'pending_orders': pending_orders,
        'avg_rating': avg_rating,
        'system_status': 'Operational' if all_users and total_orders >= 0 else 'Needs review',
    }
    return render(request, 'canteen/admin_system_health.html', context)


@login_required(login_url='login')
def add_admin(request):
    """Allow an existing admin to create another admin account."""
    if not is_admin_user(request.user):
        messages.error(request, 'Access denied. This page is only for administrators.')
        return redirect('dashboard')

    if request.method == 'POST':
        form = AdminCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            ActivityLog.objects.create(
                user=request.user,
                action='registration',
                description=f'Created new administrator: {user.username}',
                ip_address=get_client_ip(request),
            )
            messages.success(request, f'Admin account for {user.username} was created successfully.')
            return redirect('admin_add_admin')
    else:
        form = AdminCreationForm()

    return render(request, 'canteen/add_admin.html', {'form': form, 'user': request.user})


@login_required(login_url='login')
def admin_users(request):
    """Show all registered users for the admin dashboard."""
    if not is_admin_user(request.user):
        messages.error(request, 'Access denied. This page is only for administrators.')
        return redirect('dashboard')

    users = User.objects.select_related('profile').all().order_by('-date_joined')
    return render(request, 'canteen/admin_users.html', {
        'users': users,
        'user': request.user,
    })


@login_required(login_url='login')
def admin_user_edit(request, user_id):
    """Admin can update a user profile and role."""
    if not is_admin_user(request.user):
        messages.error(request, 'Access denied. This page is only for administrators.')
        return redirect('dashboard')

    target_user = User.objects.filter(pk=user_id).first()
    if target_user is None:
        messages.error(request, 'User not found.')
        return redirect('admin_users')

    profile, _ = UserProfile.objects.get_or_create(user=target_user)

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        user_type = request.POST.get('user_type')

        if not first_name or not last_name or not email:
            messages.error(request, 'First name, last name, and email are required.')
            return redirect('admin_user_edit', user_id=user_id)

        target_user.first_name = first_name
        target_user.last_name = last_name
        target_user.email = email

        if user_type in dict(UserProfile.USER_TYPE_CHOICES):
            profile.user_type = user_type
            profile.save()
        elif target_user.is_superuser or target_user.is_staff:
            profile.user_type = 'admin'
            profile.save()

        target_user.save()
        messages.success(request, f'User {target_user.username} updated successfully.')
        return redirect('admin_users')

    return render(request, 'canteen/admin_user_edit.html', {
        'user': target_user,
        'profile': profile,
        'user_types': UserProfile.USER_TYPE_CHOICES,
    })


@login_required(login_url='login')
def admin_user_delete(request, user_id):
    """Admin can delete a user account."""
    if not is_admin_user(request.user):
        messages.error(request, 'Access denied. This page is only for administrators.')
        return redirect('dashboard')

    if request.user.pk == user_id:
        messages.error(request, 'You cannot delete your own admin account.')
        return redirect('admin_users')

    target_user = User.objects.filter(pk=user_id).first()
    if target_user is None:
        messages.error(request, 'User not found.')
        return redirect('admin_users')

    username = target_user.username
    target_user.delete()
    messages.success(request, f'User {username} was deleted successfully.')
    return redirect('admin_users')


def canteen_staff_dashboard(request):
    """Canteen staff dashboard with menu, orders, and offers"""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)

    today = timezone.localdate()
    menu_items = MenuItem.objects.filter(date=today).order_by('meal_type')[:10]
    active_offers = FoodOffer.objects.filter(offer_date=today, status='active')
    pending_orders = FoodOrder.objects.filter(status='pending').order_by('-created_at')[:10]
    notifications_count = OrderNotification.objects.filter(recipient=request.user).count()
    feedback_list = UserFeedback.objects.select_related('user', 'menu_item').all()[:10]
    avg_rating = UserFeedback.objects.aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0

    context = {
        'user': request.user,
        'profile': profile,
        'menu_items': menu_items,
        'active_offers': active_offers,
        'pending_orders': pending_orders,
        'feedback_list': feedback_list,
        'avg_rating': avg_rating,
        'notifications_count': notifications_count,
    }
    return render(request, 'canteen/canteen_staff_dashboard.html', context)


@login_required(login_url='login')
def menu_management(request, item_id=None):
    """Menu upload/update management for canteen staff"""
    if request.user.profile.user_type != 'canteen_staff':
        messages.error(request, 'Access denied. This section is only for canteen staff.')
        return redirect('dashboard')

    menu_items = MenuItem.objects.all().order_by('-date', 'meal_type')
    item = None
    if item_id is not None:
        item = MenuItem.objects.filter(pk=item_id).first()
        if item is None:
            messages.error(request, 'Menu item not found.')
            return redirect('staff_menu')

    if request.method == 'POST':
        if not menu_update_window_open():
            messages.error(request, 'Menu updates are allowed only before 9:00 AM.')
            form = MenuItemForm(request.POST, instance=item)
            return render(request, 'canteen/staff_menu_management.html', {
                'form': form, 'menu_items': menu_items, 'editing_item': item,
            })

        form = MenuItemForm(request.POST, instance=item)
        if form.is_valid():
            saved_item = form.save()
            ActivityLog.objects.create(
                user=request.user,
                action='menu_update' if item else 'menu_upload',
                description=f'Menu item {"updated" if item else "created"}: {saved_item.name}',
                ip_address=get_client_ip(request),
            )
            messages.success(request, 'Menu item updated successfully.' if item else 'Menu item saved successfully.')
            return redirect('staff_menu')
    else:
        form = MenuItemForm(instance=item)

    return render(request, 'canteen/staff_menu_management.html', {
        'form': form, 'menu_items': menu_items, 'editing_item': item,
    })


@login_required(login_url='login')
def delete_menu_item(request, item_id):
    """Delete a menu item from the staff menu list."""
    if request.user.profile.user_type != 'canteen_staff':
        messages.error(request, 'Access denied. This section is only for canteen staff.')
        return redirect('dashboard')

    item = MenuItem.objects.filter(pk=item_id).first()
    if item is None:
        messages.error(request, 'Menu item not found.')
        return redirect('staff_menu')

    if request.method == 'POST':
        item_name = item.name
        item.delete()
        ActivityLog.objects.create(
            user=request.user,
            action='menu_update',
            description=f'Menu item removed: {item_name}',
            ip_address=get_client_ip(request),
        )
        messages.success(request, f'Menu item "{item_name}" was removed successfully.')

    return redirect('staff_menu')


@login_required(login_url='login')
def offer_management(request):
    """Offer creation and management for canteen staff"""
    if request.user.profile.user_type != 'canteen_staff':
        messages.error(request, 'Access denied. This section is only for canteen staff.')
        return redirect('dashboard')

    offers = FoodOffer.objects.all().order_by('-offer_date')

    if request.method == 'POST':
        form = FoodOfferForm(request.POST)
        if form.is_valid():
            offer = form.save(commit=False)
            offer.created_by = request.user
            offer.save()
            ActivityLog.objects.create(
                user=request.user,
                action='offer_created',
                description=f'Offer created: {offer.name}',
                ip_address=get_client_ip(request),
            )
            messages.success(request, 'Offer created successfully.')
            return redirect('staff_offers')
    else:
        form = FoodOfferForm()

    return render(request, 'canteen/staff_offer_management.html', {'form': form, 'offers': offers})


@login_required(login_url='login')
def order_management(request):
    """View and manage food orders"""
    if not (request.user.profile.user_type in ['canteen_staff', 'admin'] or request.user.is_staff or request.user.is_superuser):
        messages.error(request, 'Access denied. This section is only for staff and admin.')
        return redirect('dashboard')

    orders = FoodOrder.objects.select_related('user', 'user__profile', 'menu_item', 'offer').all()
    search_query = request.GET.get('q', '').strip()
    search_by = request.GET.get('search_by', 'all')
    search_fields = {
        'all',
        'admission',
        'order_id',
        'customer',
        'item',
        'status',
        'notes',
    }
    if search_by not in search_fields:
        return HttpResponseBadRequest('Select a valid order search field.')

    selected_status = request.GET.get('status', '')
    valid_statuses = dict(FoodOrder.STATUS_CHOICES)
    if selected_status and selected_status not in valid_statuses:
        return HttpResponseBadRequest('Select a valid order status.')

    if search_query:
        search_filters = {
            'admission': Q(user__profile__admission_id__icontains=search_query),
            'customer': (
                Q(user__username__icontains=search_query)
                | Q(user__first_name__icontains=search_query)
                | Q(user__last_name__icontains=search_query)
                | Q(user__email__icontains=search_query)
            ),
            'item': (
                Q(menu_item__name__icontains=search_query)
                | Q(offer__name__icontains=search_query)
            ),
            'status': Q(status__icontains=search_query),
            'notes': Q(notes__icontains=search_query),
        }
        if search_by == 'all':
            search_filter = (
                search_filters['customer']
                | search_filters['admission']
                | search_filters['item']
                | search_filters['status']
                | search_filters['notes']
            )
            if search_query.isdigit():
                search_filter |= Q(id=int(search_query))
        elif search_by == 'order_id':
            search_filter = Q(id=int(search_query)) if search_query.isdigit() else Q(pk__in=[])
        else:
            search_filter = search_filters[search_by]
        orders = orders.filter(search_filter)
    if selected_status:
        orders = orders.filter(status=selected_status)
    orders = orders.order_by('-created_at')
    if request.method == 'POST':
        order_id = request.POST.get('order_id')
        try:
            order = FoodOrder.objects.get(id=order_id)
        except FoodOrder.DoesNotExist:
            messages.error(request, 'Invalid order selected.')
            return redirect('staff_orders')

        new_status = request.POST.get('status')
        if order.status == 'delivered':
            messages.error(request, 'Delivered orders cannot be changed.')
            return redirect('staff_orders')

        allowed_statuses = dict(FoodOrder.STATUS_CHOICES)
        allowed_statuses.pop('delivered')
        if new_status in allowed_statuses:
            was_confirmed = order.status == 'confirmed'
            order.status = new_status
            order.save()
            if new_status == 'confirmed' and not was_confirmed:
                notify_order_confirmed(order)
            messages.success(request, f'Order {order.id} updated to {new_status}.')
            return redirect('staff_orders')
        messages.error(request, 'Invalid order status. Use the delivery button to confirm delivery.')
        return redirect('staff_orders')

    return render(request, 'canteen/staff_order_management.html', {
        'orders': orders,
        'search_query': search_query,
        'search_by': search_by,
        'search_fields': (
            ('all', 'All fields'),
            ('admission', 'Admission number'),
            ('order_id', 'Order ID'),
            ('customer', 'Customer'),
            ('item', 'Item'),
            ('status', 'Order status'),
            ('notes', 'Notes'),
        ),
        'selected_status': selected_status,
        'status_choices': FoodOrder.STATUS_CHOICES,
    })


@login_required(login_url='login')
def orders_by_date(request):
    """Show staff orders for a selected delivery date."""
    if not (request.user.profile.user_type in ['canteen_staff', 'admin'] or request.user.is_staff or request.user.is_superuser):
        messages.error(request, 'Access denied. This section is only for staff and admin.')
        return redirect('dashboard')

    date_value = request.GET.get('date')
    selected_date = parse_date(date_value) if date_value else timezone.localdate()
    if selected_date is None:
        return HttpResponseBadRequest('Enter a valid date in YYYY-MM-DD format.')

    selected_status = request.GET.get('status', '')
    valid_statuses = dict(FoodOrder.STATUS_CHOICES)
    if selected_status and selected_status not in valid_statuses:
        return HttpResponseBadRequest('Select a valid order status.')

    orders = FoodOrder.objects.select_related(
        'user', 'user__profile', 'menu_item', 'offer',
    ).filter(delivery_date=selected_date).order_by('created_at')
    if selected_status:
        orders = orders.filter(status=selected_status)
    return render(request, 'canteen/staff_orders_by_date.html', {
        'orders': orders,
        'selected_date': selected_date,
        'selected_status': selected_status,
        'status_choices': FoodOrder.STATUS_CHOICES,
    })


@login_required(login_url='login')
def orders_by_month(request):
    """Show staff orders for a selected delivery month."""
    if not (request.user.profile.user_type in ['canteen_staff', 'admin'] or request.user.is_staff or request.user.is_superuser):
        messages.error(request, 'Access denied. This section is only for staff and admin.')
        return redirect('dashboard')

    month_value = request.GET.get('month', timezone.localdate().strftime('%Y-%m'))
    month_start = parse_month_value(month_value)
    if month_start is None:
        return HttpResponseBadRequest('Enter a valid month in YYYY-MM format.')
    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)

    selected_status = request.GET.get('status', '')
    valid_statuses = dict(FoodOrder.STATUS_CHOICES)
    if selected_status and selected_status not in valid_statuses:
        return HttpResponseBadRequest('Select a valid order status.')

    orders = FoodOrder.objects.select_related(
        'user', 'user__profile', 'menu_item', 'offer',
    ).filter(
        delivery_date__gte=month_start,
        delivery_date__lt=month_end,
    ).order_by('created_at')
    if selected_status:
        orders = orders.filter(status=selected_status)
    return render(request, 'canteen/staff_orders_by_month.html', {
        'orders': orders,
        'selected_month': month_start,
        'selected_status': selected_status,
        'status_choices': FoodOrder.STATUS_CHOICES,
    })


@login_required(login_url='login')
def feedback_management(request):
    """View user food feedback"""
    if not (request.user.profile.user_type in ['canteen_staff', 'admin'] or request.user.is_staff or request.user.is_superuser):
        messages.error(request, 'Access denied. This section is only for staff and admin.')
        return redirect('dashboard')

    feedback_list = UserFeedback.objects.select_related('user', 'menu_item', 'offer').all().order_by('-created_at')
    return render(request, 'canteen/staff_feedback_management.html', {'feedback_list': feedback_list})


@login_required(login_url='login')
def submit_feedback(request):
    """Allow an authenticated user to submit and review their feedback."""
    if request.method == 'POST':
        form = UserFeedbackForm(request.POST)
        if form.is_valid():
            feedback = form.save(commit=False)
            feedback.user = request.user
            feedback.save()
            ActivityLog.objects.create(
                user=request.user,
                action='feedback_submitted',
                description='Submitted food feedback',
                ip_address=get_client_ip(request),
            )
            messages.success(request, 'Your feedback was submitted successfully.')
            return redirect('submit_feedback')
    else:
        form = UserFeedbackForm()

    feedback_list = UserFeedback.objects.filter(user=request.user).select_related(
        'menu_item', 'offer'
    ).order_by('-created_at')
    return render(request, 'canteen/submit_feedback.html', {
        'form': form,
        'feedback_list': feedback_list,
    })


@login_required(login_url='login')
def profile(request):
    """User profile view"""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)
    
    if request.method == 'POST':
        form = UserProfileForm(request.POST)
        if form.is_valid():
            request.user.first_name = form.cleaned_data['first_name']
            request.user.last_name = form.cleaned_data['last_name']
            request.user.email = form.cleaned_data['email']
            request.user.save()
            messages.success(request, 'Profile updated successfully!')
            return redirect('profile')
    else:
        form = UserProfileForm(initial={
            'first_name': request.user.first_name,
            'last_name': request.user.last_name,
            'email': request.user.email,
        })
    
    context = {
        'form': form,
        'profile': profile,
    }
    return render(request, 'canteen/profile.html', context)


@login_required(login_url='login')
def change_password(request):
    """Change password view"""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)

    if request.method == 'POST':
        form = ChangePasswordForm(request.POST)
        if form.is_valid():
            old_password = form.cleaned_data['old_password']
            new_password = form.cleaned_data['new_password1']

            if not request.user.check_password(old_password):
                messages.error(request, 'Current password is incorrect.')
            elif request.user.check_password(new_password):
                messages.error(request, 'New password must be different from your current password.')
            else:
                request.user.set_password(new_password)
                request.user.save(update_fields=['password'])
                login(request, request.user)
                messages.success(request, 'Password changed successfully!')
                return redirect('profile')
    else:
        form = ChangePasswordForm()

    context = {
        'form': form,
        'user': request.user,
        'profile': profile,
    }
    return render(request, 'canteen/change_password.html', context)