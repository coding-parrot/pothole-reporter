// Three of the 40 real Bengaluru pothole locations of the 6 Oct 2026 run. Not a test file
// itself: npm test only picks up *.test.mjs.
//
// What Nominatim answered for each point on 6 Oct 2026, and the ward KGIS put it in.
export const CASES = {
  munnekolala: {
    lat: 12.94572, lng: 77.71055, ward: "Munnenkolalu", ward_no: "41",
    address: { road: "6th Cross Road", quarter: "Munekolala", suburb: "Munnenkolalu", city: "Bengaluru", state: "Karnataka", postcode: "560037" },
    street: "6th Cross Road, Munnenkolalu, Bengaluru, 560037",
    expected: ["Improvements to roads and drains in Munnekolala colony at Munnekolala ward no.105"],
  },
  thubarahalli: {
    lat: 12.95906, lng: 77.7207, ward: "Kundalahalli", ward_no: "37",
    address: { road: "8th Main Road", neighbourhood: "Thubarahalli Palya", quarter: "BEML Layout 6th Stage", suburb: "Kundalahalli", city: "Bengaluru", state: "Karnataka", postcode: "560066" },
    street: "8th Main Road, Thubarahalli Palya, Kundalahalli, Bengaluru, 560066",
    expected: [
      "Improvements to Roads and Drains at Thubarahalli in AECS layoutward no.102",
      "Improvements to roads and drains in Tubarahalli extension and surrounding areas in AECS Layout ward no.102",
    ],
  },
  coxTown: {
    lat: 12.99657, lng: 77.62034, ward: "Cox Town", ward_no: "10",
    address: { road: "Thambhuchetty Road", neighbourhood: "Doddigunta", quarter: "Cox Town", suburb: "Cox Town", city: "Bengaluru", state: "Karnataka", postcode: "560005" },
    street: "Thambhuchetty Road, Doddigunta, Cox Town, Bengaluru, 560005",
    expected: ["Improvements to Roads and Drain at Doddigunta Coxtown and Surrounding area in Ward no.108"],
  },
};

// Live on 6 Oct 2026, after the first ward tender release: a street in the Gandhi Nagar
// of Munnekolala was answered with the Gandhinagaras of Yelahanka (25 km north) and of
// Kengeri (the other side of the city).
export const GANDHI_NAGAR = {
  lat: 12.9547, lng: 77.7113, ward: "Munnenkolalu", ward_no: "41",
  address: { road: "2nd Cross Road", neighbourhood: "Gandhi Nagar", suburb: "Munnenkolalu", city: "Bengaluru", state: "Karnataka", postcode: "560037" },
  street: "2nd Cross Road, Gandhi Nagar, Munnenkolalu, Bengaluru, 560037",
  expected: [
    "Improvements to roads and drains in Munnekolala colony at Munnekolala ward no.105",
    "Improvements to roads and drains in Ambedkar colony at Munnekolala ward no.105",
  ],
  yelahanka: "Resurfacing of Gandhinagara 1st main and cross roads and Nehru nagara in kempegowda ward no 01 of yelahanka Sub division",
  kengeri: "Comprehensive Development of Roads and Drains at Gandhinagara Mini Gandhinagara Bapuji Colony Kengeri Kote and Arundhathi Nagara i",
};
