"""First-run sample data: categories, services, staff, a coupon, gallery pictures."""
import os
from datetime import date, timedelta

from models import db, ServiceCategory, Service, Staff, Coupon, Gallery, SalonSettings
from utils.helpers import make_art

CATEGORIES = [  # name, icon, colour 1, colour 2
    ("Hair", "bi-scissors", (22, 48, 47), (200, 164, 107)),
    ("Men's Grooming", "bi-person-badge", (30, 41, 59), (120, 135, 150)),
    ("Women's Beauty", "bi-flower1", (140, 84, 102), (232, 201, 204)),
    ("Facial", "bi-stars", (63, 107, 98), (214, 232, 224)),
    ("Spa", "bi-droplet-half", (24, 72, 79), (150, 190, 180)),
    ("Spa Packages", "bi-gift", (96, 64, 90), (214, 170, 150)),
]

SERVICES = [  # category, name, minutes, price, discount price, description
    ("Hair", "Haircut", 60, 600, None, "Consultation, wash, precision cut and blow-dry styled to suit your face shape."),
    ("Hair", "Hair Spa", 60, 1200, 999, "Deep-conditioning treatment with scalp massage for smooth, shiny hair."),
    ("Hair", "Hair Coloring", 120, 2800, 2400, "Global colour or highlights with ammonia-free colour and a nourishing finish."),
    ("Men's Grooming", "Men's Haircut", 30, 400, None, "Classic or modern cut finished with a neat style."),
    ("Men's Grooming", "Beard Trim", 30, 300, None, "Shaping and trimming with a hot towel finish."),
    ("Women's Beauty", "Threading", 30, 150, None, "Eyebrows, upper lip and forehead, done gently."),
    ("Women's Beauty", "Waxing", 60, 900, 799, "Full arms and legs with skin-friendly wax."),
    ("Women's Beauty", "Manicure", 60, 700, None, "Nail shaping, cuticle care, hand massage and polish."),
    ("Women's Beauty", "Pedicure", 60, 800, None, "Foot soak, scrub, nail care, massage and polish."),
    ("Women's Beauty", "Makeup", 90, 3500, 2999, "Party or occasion makeup with a trial-quality finish."),
    ("Facial", "Facial", 60, 1200, None, "Cleansing, exfoliation, massage and a mask suited to your skin type."),
    ("Facial", "Hydra Glow Facial", 60, 2200, 1899, "Intense hydration and brightening for a fresh, even glow."),
    ("Spa", "Swedish Massage", 60, 2500, None, "Long, flowing strokes that ease tension and improve circulation."),
    ("Spa", "Deep Tissue Massage", 60, 3000, None, "Firm pressure aimed at knots and chronic muscle tightness."),
    ("Spa", "Aromatherapy", 60, 2800, None, "A gentle massage with essential oils chosen for your mood."),
    ("Spa", "Thai Massage", 90, 3200, None, "Stretching and acupressure performed on a comfortable mat."),
    ("Spa", "Hot Stone Massage", 90, 3800, 3299, "Warm basalt stones melt away stiffness in back and shoulders."),
    ("Spa", "Full Body Massage", 90, 3500, None, "Head-to-toe relaxation massage with warm oil."),
    ("Spa Packages", "Bridal Glow Package", 240, 12000, 9999, "Facial, makeup, manicure and pedicure for your big day."),
    ("Spa Packages", "Relax & Rejuvenate Package", 180, 6500, 5499, "Swedish massage, facial and hair spa in one visit."),
    ("Spa Packages", "Couple's Retreat", 120, 8000, None, "Two side-by-side 60-minute massages with herbal tea. Book with any therapist."),
]

STAFF = [  # name, designation, days, start, end, categories they cover
    ("Anjali Deshmukh", "Senior Hair Stylist", "0,2,3,4,5,6", "10:00", "19:00", ["Hair", "Spa Packages"]),
    ("Rohan Kulkarni", "Men's Grooming Expert", "0,1,2,3,4,5", "10:00", "20:00", ["Men's Grooming", "Hair"]),
    ("Meera Joshi", "Beauty & Facial Specialist", "0,2,3,4,5,6", "10:00", "19:00", ["Facial", "Women's Beauty", "Spa Packages"]),
    ("Kabir Shah", "Senior Massage Therapist", "0,2,3,4,5,6", "11:00", "20:00", ["Spa", "Spa Packages"]),
    ("Sneha Patil", "Spa Therapist", "0,2,3,4,5", "10:00", "18:00", ["Spa", "Facial"]),
]

GALLERY = [("Treatment room", (24, 72, 79), (150, 190, 180)), ("Reception", (22, 48, 47), (200, 164, 107)),
           ("Styling floor", (140, 84, 102), (232, 201, 204)), ("Massage suite", (63, 107, 98), (214, 232, 224)),
           ("Nail bar", (96, 64, 90), (214, 170, 150)), ("Relaxation lounge", (30, 41, 59), (120, 135, 150))]


def seed(upload_folder):
    SalonSettings.get()
    if ServiceCategory.query.count() > 0:
        return
    os.makedirs(upload_folder, exist_ok=True)

    cats = {}
    for i, (name, icon, c1, c2) in enumerate(CATEGORIES):
        filename = f"cat_{i + 1}.jpg"
        make_art(os.path.join(upload_folder, filename), c1, c2, seed=i)
        cat = ServiceCategory(name=name, icon=icon, image=filename)
        db.session.add(cat)
        cats[name] = cat

    for cat_name, name, minutes, price, disc, desc in SERVICES:
        db.session.add(Service(category=cats[cat_name], name=name, duration=minutes, price=price,
                               discount_price=disc, description=desc, image=cats[cat_name].image))
    db.session.flush()

    for name, role, days, start, end, cat_names in STAFF:
        member = Staff(name=name, designation=role, working_days=days, start_time=start, end_time=end)
        member.services = [s for s in Service.query.all() if s.category.name in cat_names]
        db.session.add(member)

    db.session.add(Coupon(code="WELCOME10", kind="percent", value=10, min_amount=500,
                          expiry=date.today() + timedelta(days=365)))

    for i, (title, c1, c2) in enumerate(GALLERY):
        filename = f"gallery_{i + 1}.jpg"
        make_art(os.path.join(upload_folder, filename), c1, c2, seed=50 + i)
        db.session.add(Gallery(title=title, image=filename))
    db.session.commit()
