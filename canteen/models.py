from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import datetime, time


def default_menu_closing_time():
    return timezone.make_aware(datetime.combine(timezone.localdate(), time(20, 0)))


def default_menu_opening_time():
    return timezone.make_aware(datetime.combine(timezone.localdate(), time(8, 0)))


def default_offer_end_time():
    return timezone.make_aware(datetime.combine(timezone.localdate(), time(15, 0)))


class UserProfile(models.Model):
    """User profile model for storing additional user information"""
    USER_TYPE_CHOICES = [
        ('student', 'Student'),
        ('faculty', 'Faculty'),
        ('day_scholar', 'Day Scholar'),
        ('staff', 'Non-Teaching Staff'),
        ('canteen_staff', 'Canteen Staff'),
        ('admin', 'Administrator'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default='student')
    admission_id = models.CharField(max_length=20, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.get_user_type_display()}"

    class Meta:
        verbose_name_plural = "User Profiles"


class MenuItem(models.Model):
    """Daily menu item model"""
    MEAL_TYPE_CHOICES = [
        ('breakfast', 'Breakfast'),
        ('lunch', 'Lunch'),
        ('snacks', 'Snacks'),
        ('dinner', 'Dinner'),
    ]
    
    AVAILABILITY_STATUS = [
        ('available', 'Available'),
        ('out_of_stock', 'Out of Stock'),
        ('discontinued', 'Discontinued'),
    ]
    
    name = models.CharField(max_length=100)
    description = models.TextField()
    category = models.CharField(max_length=50)
    meal_type = models.CharField(max_length=20, choices=MEAL_TYPE_CHOICES)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    available_quantity = models.IntegerField()
    date = models.DateField()
    opening_time = models.DateTimeField(default=default_menu_opening_time)
    closing_time = models.DateTimeField(default=default_menu_closing_time)
    auto_approve_orders = models.BooleanField(default=False)
    availability_status = models.CharField(max_length=20, choices=AVAILABILITY_STATUS, default='available')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.date}"

    class Meta:
        ordering = ['-date', 'meal_type']


class ActivityLog(models.Model):
    """Track all user activities in the system"""
    ACTION_CHOICES = [
        ('login', 'User Login'),
        ('logout', 'User Logout'),
        ('registration', 'User Registration'),
        ('menu_upload', 'Menu Uploaded'),
        ('menu_update', 'Menu Updated'),
        ('offer_created', 'Offer Created'),
        ('offer_updated', 'Offer Updated'),
        ('order_placed', 'Order Placed'),
        ('order_cancelled', 'Order Cancelled'),
        ('feedback_submitted', 'Feedback Submitted'),
        ('price_updated', 'Price Updated'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activity_logs')
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    description = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    
    def __str__(self):
        return f"{self.user.username} - {self.action} - {self.timestamp}"
    
    class Meta:
        ordering = ['-timestamp']


class FoodOffer(models.Model):
    """Special food offers with time restrictions"""
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('expired', 'Expired'),
    ]
    
    menu_item = models.ForeignKey(MenuItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='offers')
    name = models.CharField(max_length=100)
    description = models.TextField()
    original_price = models.DecimalField(max_digits=8, decimal_places=2)
    discounted_price = models.DecimalField(max_digits=8, decimal_places=2)
    discount_percentage = models.IntegerField(default=0)
    available_quantity = models.IntegerField()
    offer_date = models.DateField()
    start_time = models.TimeField(default='13:30')  # 1:30 PM
    end_time = models.DateTimeField(default=default_offer_end_time)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='food_offers')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.name} - {self.offer_date}"
    
    class Meta:
        ordering = ['-offer_date', 'start_time']


class FoodOrder(models.Model):
    """Track food orders from users"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('ready', 'Ready for Delivery'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='food_orders')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.SET_NULL, null=True, blank=True)
    offer = models.ForeignKey(FoodOffer, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.IntegerField()
    total_price = models.DecimalField(max_digits=8, decimal_places=2)
    delivery_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        item_name = self.menu_item.name if self.menu_item else self.offer.name
        return f"{self.user.username} - {item_name} - {self.delivery_date}"
    
    class Meta:
        ordering = ['-created_at']


class OrderNotification(models.Model):
    """In-app order confirmation notification for an individual account."""
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='order_notifications')
    order = models.ForeignKey(FoodOrder, on_delete=models.CASCADE, related_name='notifications')
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class UserFeedback(models.Model):
    """User feedback about food items"""
    RATING_CHOICES = [
        (1, '⭐ Poor'),
        (2, '⭐⭐ Fair'),
        (3, '⭐⭐⭐ Good'),
        (4, '⭐⭐⭐⭐ Very Good'),
        (5, '⭐⭐⭐⭐⭐ Excellent'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='feedbacks')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='feedbacks')
    offer = models.ForeignKey(FoodOffer, on_delete=models.SET_NULL, null=True, blank=True, related_name='feedbacks')
    rating = models.IntegerField(choices=RATING_CHOICES)
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.user.username} - {self.rating} stars"
    
    class Meta:
        ordering = ['-created_at']


class   AdminSession(models.Model):
    """Track admin login sessions (only one admin can be logged in)"""
    admin_user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='admin_session')
    login_time = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return f"Admin: {self.admin_user.username} - Active: {self.is_active}"


class CanteenStaffSession(models.Model):
    """Track canteen staff login sessions (only one staff can be logged in)"""
    staff_user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_session')
    login_time = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return f"Staff: {self.staff_user.username} - Active: {self.is_active}"
