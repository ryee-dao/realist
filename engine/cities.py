"""The cities the engine covers and where each one's data comes from."""
from . import context
from .sources import chicago, nyc, philadelphia

CITIES = {
    "philadelphia": {
        "name": "Philadelphia", "state": "PA", "center": [39.99, -75.13], "zoom": 11,
        "area_noun": "Neighborhood", "area_by": "area",
        # area_names: display-name overrides. With area_by "area" they map the boundary file's name to the label
        # shown on the site; the area key (and so every id and link) keeps the boundary name.
        # The boundary file calls Port Richmond just "Richmond".
        "area_names": {"Richmond": "Port Richmond"},
        # the sales files carry no neighbourhood, so each sale is placed by its coordinates in the city's boundaries
        "boundaries": {"url": "https://raw.githubusercontent.com/opendataphilly/open-geo-data/master/philadelphia-neighborhoods/philadelphia-neighborhoods.geojson",
                       "name": "MAPNAME"},
        "harvest": philadelphia.harvest,
        "sources": [
            {"name": "Real estate transfers", "publisher": "City of Philadelphia, Department of Records",
             "url": "https://opendataphilly.org/datasets/real-estate-transfers/"},
            {"name": "Property assessments", "publisher": "City of Philadelphia, Office of Property Assessment",
             "url": "https://opendataphilly.org/datasets/philadelphia-properties-and-assessment-history/"},
            {"name": "Neighborhood boundaries", "publisher": "OpenDataPhilly",
             "url": "https://opendataphilly.org/datasets/philadelphia-neighborhoods/"},
        ],
        "notes": "Sale prices are the recorded deed consideration. Size, type and assessed value come from the "
                 "city's current property record for the parcel. Sales newer than the latest published deeds come "
                 "from property records. Condos are left out: the city's files do not identify condo sales "
                 "consistently enough to chart them.",
    },
    "nyc": {
        "name": "New York City", "state": "NY", "center": [40.71, -73.95], "zoom": 10,
        "area_noun": "Neighborhood", "area_by": "area", "area_names": {},
        "harvest": nyc.harvest, "prepare": nyc.prepare_rows,
        "sources": [
            {"name": "Citywide annualized calendar sales", "publisher": "NYC Department of Finance",
             "url": "https://data.cityofnewyork.us/d/w2pb-icbu"},
            {"name": "Citywide rolling calendar sales", "publisher": "NYC Department of Finance",
             "url": "https://data.cityofnewyork.us/d/usep-8jbt"},
            {"name": "Recent deeds (ACRIS)", "publisher": "NYC Department of Finance",
             "url": "https://data.cityofnewyork.us/d/bnx9-e6tj"},
            {"name": "Tax lot building classes and locations (PLUTO)", "publisher": "NYC Department of City Planning",
             "url": "https://data.cityofnewyork.us/d/64uk-42ks"},
        ],
        "notes": "Co-ops and condos are sold by unit and the city does not publish unit floor area, so price per "
                 "square foot covers one- to three-family homes only. Sales newer than the Finance files come from "
                 "recorded deeds (ACRIS), which cover houses and condos but not co-ops or Staten Island; Finance's own record replaces "
                 "them once it reaches those dates. A deed covering several lots is left out, as its price is not "
                 "per home.",
    },
    "chicago": {
        "name": "Chicago", "state": "IL", "center": [41.84, -87.68], "zoom": 10,
        "area_noun": "Community area", "area_by": "area", "area_names": {},
        "harvest": chicago.harvest,
        "sources": [
            {"name": "Assessor parcel sales", "publisher": "Cook County Assessor's Office",
             "url": "https://datacatalog.cookcountyil.gov/d/wvhk-k5uv"},
            {"name": "Parcel universe, addresses and characteristics", "publisher": "Cook County Assessor's Office",
             "url": "https://datacatalog.cookcountyil.gov/d/pabr-t5kh"},
        ],
        "notes": "Covers the City of Chicago townships. Building size is published for houses and small "
                 "multi-family buildings, not for condo units.",
    },
}

context.CITIES_BOUNDARIES.update({city: cfg["boundaries"] for city, cfg in CITIES.items() if cfg.get("boundaries")})

# Asking-side listing figures by ZIP code, shown apart from recorded sales (see context.harvest_listings)
LISTINGS_SOURCE = {"name": "Listings by ZIP code", "publisher": "Zillow Research", "url": "https://www.zillow.com/research/data/"}
for _cfg in CITIES.values():
    _cfg["sources"].append(LISTINGS_SOURCE)
