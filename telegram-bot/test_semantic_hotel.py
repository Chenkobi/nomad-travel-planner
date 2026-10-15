import unittest
from semantic_hotel import understand_hotel_reservation

class SemanticHotelTests(unittest.TestCase):
 def test_confirmation_facts_and_evidence(self):
  r=understand_hotel_reservation("Reservation confirmation","Hotel: Harbor View Hotel\nCity: Lisbon\nCheck-in: 12 April 2027\nCheck-out: 15 April 2027\nCancellation policy: free cancellation")
  self.assertEqual(r["intent"],"reservation"); self.assertEqual(r["decision"],"accept"); self.assertEqual(r["facts"]["check_in"]["value"],"2027-04-12"); self.assertIn("Check-in",r["facts"]["check_in"]["evidence"])
 def test_direct_minor_and_ota_shapes_are_provider_agnostic(self):
  for s,b in [("Your stay is confirmed","Property: Anantara Riverside Bangkok Resort\nArrival: 5 May 2027\nDeparture: 8 May 2027"),("Accommodation booking","Property: Lisbon Garden Hotel\nCheck-in: 05/05/2027\nCheck-out: 08/05/2027")]:
   r=understand_hotel_reservation(s,b); self.assertEqual(r["intent"],"reservation"); self.assertTrue(r["facts"]["hotel"]["value"])
 def test_html_hebrew(self):
  r=understand_hotel_reservation("אישור הזמנה","<div>מלון: מלון שקיעה</div><div>עיר: אילת</div><div>צ׳ק-אין: 12 באפריל 2027</div><div>צ׳ק-אאוט: 15 באפריל 2027</div>")
  self.assertEqual(r["decision"],"accept"); self.assertEqual(r["facts"]["check_in"]["value"],"2027-04-12")
 def test_recommendation_policy_rejected(self):
  self.assertEqual(understand_hotel_reservation("Recommended hotels","Explore recommended hotels. No booking has been made.")["decision"],"reject")
  self.assertEqual(understand_hotel_reservation("Hotel cancellation policy","Bookings may be cancelled before arrival.")["intent"],"policy")
 def test_ambiguous_or_missing_dates_review(self):
  r=understand_hotel_reservation("Reservation","Hotel: Example House\nCheck-in: 05/06/2027\nCheck-out: 08/06/2027"); self.assertEqual(r["decision"],"review"); self.assertIsNone(r["facts"]["check_in"]["value"])
  r=understand_hotel_reservation("Reservation confirmation","Hotel: Example House\nCheck-in: 12 April 2027"); self.assertEqual(r["decision"],"review"); self.assertIsNone(r["facts"]["check_out"]["value"])
if __name__=='__main__': unittest.main()
