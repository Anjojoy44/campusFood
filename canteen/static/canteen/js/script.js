// ==========================================
// MOBILE MENU TOGGLE
// ==========================================

document.addEventListener('DOMContentLoaded', function () {
    const hamburger = document.getElementById('hamburger');
    const navMenu = document.getElementById('navMenu');
    const navLinks = document.querySelectorAll('.nav-link');

    // Toggle mobile menu
    if (hamburger) {
        hamburger.addEventListener('click', function () {
            navMenu.classList.toggle('active');
            hamburger.classList.toggle('active');
        });
    }

    // Close menu when a link is clicked
    navLinks.forEach(link => {
        link.addEventListener('click', function () {
            navMenu.classList.remove('active');
            hamburger.classList.remove('active');
        });
    });

    // Close menu when clicking outside
    document.addEventListener('click', function (event) {
        if (hamburger && !hamburger.contains(event.target) && !navMenu.contains(event.target)) {
            navMenu.classList.remove('active');
            hamburger.classList.remove('active');
        }
    });
});

// ==========================================
// ACTIVE NAV LINK HIGHLIGHTING
// ==========================================

function updateActiveNavLink() {
    const navLinks = document.querySelectorAll('.nav-link');
    const sections = document.querySelectorAll('section');

    let currentSection = '';

    // Check which section is in view
    sections.forEach(section => {
        const sectionTop = section.offsetTop;
        const sectionHeight = section.clientHeight;

        if (window.scrollY >= sectionTop - 200) {
            currentSection = section.getAttribute('id');
        }
    });

    // Update active link
    navLinks.forEach(link => {
        link.classList.remove('active');
        if (link.getAttribute('href') === '#' + currentSection) {
            link.classList.add('active');
        }
    });
}

window.addEventListener('scroll', updateActiveNavLink);

// ==========================================
// SMOOTH SCROLL BEHAVIOR
// ==========================================

document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        const href = this.getAttribute('href');
        if (href !== '#' && document.querySelector(href)) {
            e.preventDefault();
            const target = document.querySelector(href);
            target.scrollIntoView({
                behavior: 'smooth',
                block: 'start'
            });
        }
    });
});

// ==========================================
// FORM VALIDATION UTILITY
// ==========================================

function validateEmail(email) {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return emailRegex.test(email);
}

function validatePassword(password) {
    // Minimum 8 characters with uppercase, lowercase, number, and special character
    const passwordRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$/;
    return passwordRegex.test(password);
}

function validatePhone(phone) {
    const phoneRegex = /^[0-9]{10}$/;
    return phoneRegex.test(phone.replace(/\D/g, ''));
}

// ==========================================
// FORM HANDLING
// ==========================================

function setupFormValidation() {
    const forms = document.querySelectorAll('form');
    
    forms.forEach(form => {
        form.addEventListener('submit', function (e) {
            let isValid = true;
            const inputs = this.querySelectorAll('input[required], textarea[required]');

            inputs.forEach(input => {
                if (!input.value.trim()) {
                    isValid = false;
                    input.classList.add('error');
                    showError(input, 'This field is required');
                } else {
                    input.classList.remove('error');
                    removeError(input);

                    // Specific validation
                    if (input.type === 'email') {
                        if (!validateEmail(input.value)) {
                            isValid = false;
                            input.classList.add('error');
                            showError(input, 'Please enter a valid email');
                        }
                    }

                    if (input.type === 'password' && input.name !== 'password' && !validatePassword(input.value)) {
                        isValid = false;
                        input.classList.add('error');
                        showError(input, 'Use 8+ characters with uppercase, lowercase, a number, and a special character');
                    }
                }
            });

            if (!isValid) {
                e.preventDefault();
            }
        });

        // Clear error on input
        const inputs = form.querySelectorAll('input, textarea');
        inputs.forEach(input => {
            input.addEventListener('input', function () {
                if (this.value.trim()) {
                    this.classList.remove('error');
                    removeError(this);
                }
            });
        });
    });

    const registrationPassword = document.getElementById('id_password1');
    if (registrationPassword) {
        const requirements = {
            length: password => password.length >= 8,
            upper: password => /[A-Z]/.test(password),
            lower: password => /[a-z]/.test(password),
            number: password => /[0-9]/.test(password),
            special: password => /[^A-Za-z0-9]/.test(password)
        };
        registrationPassword.addEventListener('input', function () {
            Object.entries(requirements).forEach(([name, isValid]) => {
                const item = document.querySelector(`[data-requirement="${name}"]`);
                const check = item.querySelector('.req-check');
                item.classList.toggle('valid', isValid(this.value));
                check.textContent = isValid(this.value) ? '✓' : '✗';
            });
        });
    }
}

function showError(element, message) {
    if (!element.nextElementSibling || !element.nextElementSibling.classList.contains('error-message')) {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'error-message';
        errorDiv.textContent = message;
        element.parentNode.insertBefore(errorDiv, element.nextSibling);
    }
}

function removeError(element) {
    if (element.nextElementSibling && element.nextElementSibling.classList.contains('error-message')) {
        element.nextElementSibling.remove();
    }
}

// ==========================================
// QUANTITY SELECTOR
// ==========================================

function setupQuantitySelectors() {
    const quantityBtns = document.querySelectorAll('.qty-minus, .qty-plus');

    quantityBtns.forEach(btn => {
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            const input = this.parentNode.querySelector('.qty-input');
            let quantity = parseInt(input.value);

            if (this.classList.contains('qty-plus')) {
                const max = parseInt(input.dataset.max) || 100;
                if (quantity < max) {
                    quantity++;
                }
            } else if (this.classList.contains('qty-minus')) {
                if (quantity > 1) {
                    quantity--;
                }
            }

            input.value = quantity;
        });
    });
}

// ==========================================
// SHOW TOAST NOTIFICATION
// ==========================================

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('show');
    }, 10);

    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => {
            toast.remove();
        }, 300);
    }, 3000);
}

// ==========================================
// CONFIRMATION DIALOG
// ==========================================

function showConfirmation(message, onConfirm, onCancel) {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';

    const modal = document.createElement('div');
    modal.className = 'confirmation-modal';
    modal.innerHTML = `
        <h3>${message}</h3>
        <div class="modal-buttons">
            <button class="btn btn-secondary" id="confirmCancel">Cancel</button>
            <button class="btn btn-primary" id="confirmOk">OK</button>
        </div>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    document.getElementById('confirmOk').addEventListener('click', function () {
        backdrop.remove();
        if (onConfirm) onConfirm();
    });

    document.getElementById('confirmCancel').addEventListener('click', function () {
        backdrop.remove();
        if (onCancel) onCancel();
    });

    backdrop.addEventListener('click', function (e) {
        if (e.target === backdrop) {
            backdrop.remove();
            if (onCancel) onCancel();
        }
    });
}

// ==========================================
// INITIALIZE ON PAGE LOAD
// ==========================================

document.addEventListener('DOMContentLoaded', function () {
    setupFormValidation();
    setupQuantitySelectors();
    updateActiveNavLink();
});

// ==========================================
// SCROLL ANIMATIONS
// ==========================================

function handleScrollAnimations() {
    const elements = document.querySelectorAll('.feature-card, .menu-item-card, .offer-card');

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.animation = 'slideInUp 0.6s ease forwards';
                observer.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.1
    });

    elements.forEach(element => {
        observer.observe(element);
    });
}

window.addEventListener('load', handleScrollAnimations);

// ==========================================
// COUNTDOWN TIMER FOR OFFERS
// ==========================================

function startOfferCountdown(elementId, expiryTime) {
    const element = document.getElementById(elementId);
    if (!element) return;

    setInterval(() => {
        const now = new Date().getTime();
        const distance = expiryTime - now;

        if (distance < 0) {
            element.innerHTML = 'Expired';
            element.style.color = '#999';
        } else {
            const minutes = Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60));
            const seconds = Math.floor((distance % (1000 * 60)) / 1000);
            element.innerHTML = `${minutes}m ${seconds}s`;
        }
    }, 1000);
}

// ==========================================
// UTILITY: COPY TO CLIPBOARD
// ==========================================

function copyToClipboard(text, message = 'Copied to clipboard!') {
    navigator.clipboard.writeText(text).then(() => {
        showToast(message, 'success');
    }).catch(() => {
        showToast('Failed to copy', 'error');
    });
}

// ==========================================
// EXPORT FUNCTIONS FOR USE IN OTHER MODULES
// ==========================================

window.FoodManagementApp = {
    validateEmail,
    validatePassword,
    validatePhone,
    showToast,
    showConfirmation,
    copyToClipboard,
    startOfferCountdown
};
