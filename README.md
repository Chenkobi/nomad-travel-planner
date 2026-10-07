# Nomad Travel Planner

אבטיפוס Web App ידידותי ל-iOS לתכנון טיול רב-יעדי.

כולל:
- לו״ז יומי ל-10 ימים עם טיסות, רכבות, מלונות והזמנות
- מסמכים: ביטוח ואישורי הזמנה
- המרת ILS / CHF / EUR / USD
- שיתוף למשפחה באמצעות קישור וקוד QR
- הדמיית ייבוא אישורים דרך בוט Telegram

## הרצה מקומית

פותחים את `index.html` בדפדפן. אין תלויות חיצוניות.

## הערת מוצר

בשלב האבטיפוס שערי המטבע הם נתוני דוגמה. חיבור אמיתי לשער יומי ולבוט Telegram ידרוש backend קטן, אימות משתמשים, אחסון מאובטח ו-parser לאישורי הזמנה.

## Email Intake

השרת כולל endpoint ראשוני לקליטת מייל MIME גולמי:

```text
POST /api/intake/email
X-TRIPY-INTAKE-SECRET: [server secret]
Content-Type: message/rfc822
```

הוא שומר את המייל המקורי, גוף הטקסט, גוף ה-HTML והקבצים המצורפים תחת `TRIPY_EMAIL_DIR`, ומונע קליטה כפולה לפי `Message-ID` או SHA-256. בשלב הבא הקליטה תחובר ל-parser של הזמנות, ביטולים ושינויים.

## API security

בפרודקשן חובה להגדיר את משתנה הסביבה `TRIPY_API_AUTH_TOKEN` בצד השרת. כל קריאות ה-API הרגישות והמשנות מצב דורשות `Authorization: Bearer ...`, כותרת `X-TRIPY-API-TOKEN`, או session cookie שנוצר בעת פתיחת ה-frontend. השרת מחזיר CORS רק למקור המוגדר ב-`TRIPY_API_ALLOWED_ORIGIN` (ברירת המחדל היא מקור ה-frontend של TRIPY), ולא משתמש ב-wildcard.

להרצה מקומית מפורשת אפשר להשאיר את `TRIPY_API_AUTH_TOKEN` לא מוגדר: השרת מאפשר את ה-API ללא אימות ומשתמש ב-CORS פתוח כדי לשמר את זרימת הפיתוח המקומית. ה-frontend שולח את כותרת האימות רק כאשר `window.TRIPY_API_AUTH_TOKEN` מוגדר בזמן ההרצה; אין להוסיף ערך סודי לקוד המקור.
