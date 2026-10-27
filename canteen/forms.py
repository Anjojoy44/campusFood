from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import UserProfile, MenuItem, FoodOffer, FoodOrder, UserFeedback


def validate_password_strength(password):
    """Require a password with a minimum length and character variety."""
    if len(password) < 8:
        raise ValidationError('Password must be at least 8 characters long.')
    if not any(char.isupper() for char in password):
        raise ValidationError('Password must contain at least one uppercase letter.')
    if not any(char.islower() for char in password):
        raise ValidationError('Password must contain at least one lowercase letter.')
    if not any(char.isdigit() for char in password):
        raise ValidationError('Password must contain at least one digit.')
    if not any(not char.isalnum() for char in password):
        raise ValidationError('Password must contain at least one special character.')
    return password


class CustomUserCreationForm(UserCreationForm):
    """Custom registration form for user signup"""
    email = forms.EmailField(
        required=True,
        help_text='Required. Enter a valid email address.'
    )
    first_name = forms.CharField(
        max_length=30,
        required=True,
        help_text='Required.'
    )
    last_name = forms.CharField(
        max_length=30,
        required=True,
        help_text='Required.'
    )
    USER_TYPE_CHOICES = [
        ('student', 'Student'),
        ('faculty', 'Faculty'),
        ('day_scholar', 'Day Scholar'),
        ('staff', 'Non-Teaching Staff'),
        ('canteen_staff', 'Canteen Staff'),
    ]
    user_type = forms.ChoiceField(
        choices=USER_TYPE_CHOICES,
        required=True,
        help_text='Required. Select your role.'
    )

    admission_id = forms.CharField(
        required = True,
        max_length=20,
        help_text='Required. Used to identify you in order notifications.'
    )

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'user_type', 'username', 'password1', 'password2', 'admission_id')

    def clean_email(self):
        """Validate that email is unique"""
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise ValidationError('This email address is already registered.')
        return email

    def clean_username(self):
        """Validate username"""
        username = self.cleaned_data.get('username')
        if User.objects.filter(username=username).exists():
            raise ValidationError('This username is already taken.')
        if len(username) < 3:
            raise ValidationError('Username must be at least 3 characters long.')
        return username

    def clean_password2(self):
        """Validate password confirmation"""
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        
        if password1 and password2 and password1 != password2:
            raise ValidationError('Passwords do not match.')
        return password2

    def clean_password1(self):
        return validate_password_strength(self.cleaned_data.get('password1', ''))

    def clean(self):
        """Perform form-wide validation"""
        cleaned_data = super().clean()
        username = cleaned_data.get('username')
        email = cleaned_data.get('email')
        
        # Double-check username uniqueness
        if username and User.objects.filter(username=username).exists():
            raise ValidationError({'username': 'This username is already taken.'})
        
        # Double-check email uniqueness
        if email and User.objects.filter(email=email).exists():
            raise ValidationError({'email': 'This email address is already registered.'})
        
        return cleaned_data

    def save(self, commit=True):
        """Save user with user_type in profile"""
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        
        if commit:
            user.save()
            # Save user_type in session or profile (we'll use profile later)
            UserProfile.objects.get_or_create(
                user=user,
                defaults={
                    'user_type': self.cleaned_data['user_type'],
                    'admission_id': self.cleaned_data['admission_id'],
                }
            )
        return user


class AdminCreationForm(UserCreationForm):
    """Admin-only account creation form for existing administrators."""
    email = forms.EmailField(required=True, help_text='Required. Enter a valid email address.')
    first_name = forms.CharField(max_length=30, required=True, help_text='Required.')
    last_name = forms.CharField(max_length=30, required=True, help_text='Required.')

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'username', 'password1', 'password2')

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise ValidationError('This email address is already registered.')
        return email

    def clean_username(self):
        username = self.cleaned_data.get('username')
        if User.objects.filter(username=username).exists():
            raise ValidationError('This username is already taken.')
        if len(username) < 3:
            raise ValidationError('Username must be at least 3 characters long.')
        return username

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise ValidationError('Passwords do not match.')
        return password2

    def clean_password1(self):
        return validate_password_strength(self.cleaned_data.get('password1', ''))

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.is_staff = True
        user.is_superuser = False

        if commit:
            user.save()
            UserProfile.objects.update_or_create(
                user=user,
                defaults={'user_type': 'admin'}
            )
        return user


class CustomAuthenticationForm(AuthenticationForm):
    """Custom login form"""
    username = forms.CharField(
        max_length=254,
        widget=forms.TextInput(attrs={
            'autofocus': True,
            'placeholder': 'Username or Email'
        })
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'current-password',
            'placeholder': 'Password',
            'minlength': 8,
        })
    )

    def clean_username(self):
        """Allow login with username or email"""
        username = self.cleaned_data.get('username')
        
        # Check if it's an email
        if '@' in username:
            try:
                user = User.objects.get(email=username)
                return user.username
            except User.DoesNotExist:
                raise ValidationError('Invalid email or password.')
        
        return username


class UserProfileForm(forms.Form):
    """Form for user profile updates"""
    first_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'First Name'})
    )
    last_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Last Name'})
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'placeholder': 'Email'})
    )


class ChangePasswordForm(forms.Form):
    """Form for changing password"""
    old_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Current Password'}),
        required=True,
        label='Current Password'
    )
    new_password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'New Password'}),
        required=True,
        label='New Password',
        min_length=8,
        help_text='At least 8 characters with uppercase, lowercase, a number, and a special character.'
    )
    new_password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={'placeholder': 'Confirm New Password'}),
        required=True,
        label='Confirm New Password'
    )

    def clean_new_password1(self):
        """Validate new password strength"""
        password = self.cleaned_data.get('new_password1')
        
        return validate_password_strength(password)

    def clean_new_password2(self):
        """Validate password confirmation"""
        new_password1 = self.cleaned_data.get('new_password1')
        new_password2 = self.cleaned_data.get('new_password2')
        
        if new_password1 and new_password2 and new_password1 != new_password2:
            raise ValidationError('Passwords do not match.')
        
        return new_password2


class MenuItemForm(forms.ModelForm):
    """Form used by canteen staff to create/update menu items."""

    class Meta:
        model = MenuItem
        fields = [
            'name', 'description', 'category', 'meal_type',
            'price', 'available_quantity', 'date',
            'opening_time', 'closing_time', 'auto_approve_orders', 'availability_status'
        ]
        labels = {
            'date': 'Food Menu Date',
            'opening_time': 'Opening Date and Time',
            'closing_time': 'Closing Date and Time',
            'auto_approve_orders': 'Auto Approve Orders',
            'availability_status': 'Availability',
        }
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'opening_time': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
            'closing_time': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
        }

    def clean_price(self):
        price = self.cleaned_data.get('price')
        if price is not None and price <= 0:
            raise ValidationError('Price must be greater than zero.')
        return price

    def clean_available_quantity(self):
        quantity = self.cleaned_data.get('available_quantity')
        if quantity is not None and quantity < 0:
            raise ValidationError('Available quantity cannot be negative.')
        return quantity

    def clean(self):
        cleaned_data = super().clean()
        opening_time = cleaned_data.get('opening_time')
        closing_time = cleaned_data.get('closing_time')

        if opening_time and closing_time and closing_time <= opening_time:
            raise ValidationError('Closing date and time must be after the opening date and time.')

        return cleaned_data


class FoodOfferForm(forms.ModelForm):
    """Form used by canteen staff to create or update offers."""

    class Meta:
        model = FoodOffer
        fields = [
            'menu_item', 'name', 'description', 'original_price', 'discounted_price',
            'discount_percentage', 'available_quantity', 'offer_date',
            'start_time', 'end_time', 'status'
        ]
        widgets = {
            'offer_date': forms.DateInput(attrs={'type': 'date'}),
            'start_time': forms.TimeInput(attrs={'type': 'time'}),
            'end_time': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['menu_item'].queryset = MenuItem.objects.all().order_by('-date', 'meal_type', 'name')
        self.fields['menu_item'].label = 'Existing menu item'
        self.fields['menu_item'].empty_label = 'Select an uploaded menu item'

    def clean(self):
        cleaned_data = super().clean()
        original_price = cleaned_data.get('original_price')
        discounted_price = cleaned_data.get('discounted_price')

        if original_price is not None and discounted_price is not None:
            if discounted_price >= original_price:
                raise ValidationError({'discounted_price': 'Discounted price must be lower than the original price.'})

        return cleaned_data

    def clean_discount_percentage(self):
        percentage = self.cleaned_data.get('discount_percentage')
        if percentage is not None and (percentage < 0 or percentage > 100):
            raise ValidationError('Discount percentage must be between 0 and 100.')
        return percentage


class FoodOrderForm(forms.ModelForm):
    """User order form for placing food orders."""

    class Meta:
        model = FoodOrder
        fields = ['menu_item', 'quantity', 'delivery_date', 'notes']
        widgets = {
            'delivery_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Optional notes'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['menu_item'].queryset = MenuItem.objects.filter(availability_status='available')
        self.fields['menu_item'].empty_label = 'Select a menu item'

    def clean_quantity(self):
        quantity = self.cleaned_data.get('quantity')
        if quantity is None or quantity < 1:
            raise ValidationError('Quantity must be at least 1.')
        return quantity

    def clean(self):
        cleaned_data = super().clean()
        menu_item = cleaned_data.get('menu_item')
        quantity = cleaned_data.get('quantity')

        if menu_item and quantity is not None and quantity > menu_item.available_quantity:
            raise ValidationError({'quantity': f'Only {menu_item.available_quantity} units are available for {menu_item.name}.'})

        return cleaned_data


class OrderStatusForm(forms.ModelForm):
    """Simple form for updating the order status."""

    class Meta:
        model = FoodOrder
        fields = ['status']


class UserFeedbackForm(forms.ModelForm):
    """Form used to submit feedback for menu items or offers."""

    class Meta:
        model = UserFeedback
        fields = ['menu_item', 'offer', 'rating', 'comment']

