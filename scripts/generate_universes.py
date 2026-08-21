"""Generate NIFTY 100, 200, and 500 JSON files from base constituents.

Run: python scripts/generate_universes.py
Outputs: config/symbols/nifty100.json, nifty200.json, nifty500.json

Each file is SELF-CONTAINED — it includes ALL stocks for that tier.
nifty100 = nifty50 + next50
nifty200 = nifty100 + next100
nifty500 = nifty200 + next300
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOLS_DIR = os.path.join(BASE_DIR, "config", "symbols")


def make_entry(symbol, isin, sector, tiers, cap_tier="large"):
    return {
        "symbol": symbol,
        "isin": isin,
        "upstox_key": f"NSE_EQ|{isin}",
        "exchange": "NSE",
        "segment": "EQ",
        "sector_id": sector,
        "index_membership": tiers,
        "market_cap_tier": cap_tier,
    }


# ── NIFTY NEXT 50 (additional 50 for NIFTY 100) ──────────────────────
NEXT_50 = [
    ("ABB", "INE117A01022", "INFRA"),
    ("ADANIGREEN", "INE364U01010", "ENERGY"),
    ("AMBUJACEM", "INE079A01024", "CEMENT"),
    ("ATGL", "INE399L01023", "ENERGY"),
    ("AUROPHARMA", "INE406A01037", "PHARMA"),
    ("BANKBARODA", "INE028A01039", "PSU_BANK"),
    ("BERGEPAINT", "INE463A01038", "CONSUMER_DURABLES"),
    ("BOSCHLTD", "INE323A01026", "AUTO"),
    ("CHOLAFIN", "INE121A01024", "FINANCIALS"),
    ("COLPAL", "INE259A01022", "FMCG"),
    ("DABUR", "INE016A01026", "FMCG"),
    ("DLF", "INE271C01023", "REALTY"),
    ("GAIL", "INE129A01019", "ENERGY"),
    ("GODREJCP", "INE102D01028", "FMCG"),
    ("HAVELLS", "INE176B01034", "CONSUMER_DURABLES"),
    ("HAL", "INE066F01020", "INFRA"),
    ("ICICIGI", "INE765G01017", "INSURANCE"),
    ("ICICIPRULI", "INE726G01019", "INSURANCE"),
    ("INDUSTOWER", "INE121J01017", "TELECOM"),
    ("IOC", "INE242A01010", "ENERGY"),
    ("IRCTC", "INE335Y01020", "INFRA"),
    ("JINDALSTEL", "INE220G01021", "METALS"),
    ("LICI", "INE0J1Y01017", "INSURANCE"),
    ("LUPIN", "INE326A01037", "PHARMA"),
    ("MARICO", "INE196A01026", "FMCG"),
    ("MOTHERSON", "INE775A01035", "AUTO"),
    ("NAUKRI", "INE663F01024", "IT"),
    ("PEL", "INE318A01026", "FINANCIALS"),
    ("PIDILITIND", "INE318A01026", "CHEMICALS"),
    ("PNB", "INE160A01022", "PSU_BANK"),
    ("POLYCAB", "INE455K01017", "CONSUMER_DURABLES"),
    ("RECLTD", "INE020B01018", "FINANCIALS"),
    ("PFC", "INE134E01011", "FINANCIALS"),
    ("SBICARD", "INE018E01016", "FINANCIALS"),
    ("SIEMENS", "INE003A01024", "INFRA"),
    ("SRF", "INE647A01024", "CHEMICALS"),
    ("TATAPOWER", "INE245A01021", "POWER"),
    ("TORNTPHARM", "INE685A01028", "PHARMA"),
    ("TRENT", "INE849A01020", "FMCG"),
    ("UNIONBANK", "INE692A01016", "PSU_BANK"),
    ("VEDL", "INE205A01025", "METALS"),
    ("VOLTAS", "INE226A01021", "CONSUMER_DURABLES"),
    ("ZOMATO", "INE758T01015", "IT"),
    ("ZYDUSLIFE", "INE010B01027", "PHARMA"),
    ("MANKIND", "INE634S01028", "PHARMA"),
    ("JSWENERGY", "INE121E01018", "POWER"),
    ("CANBK", "INE476A01014", "PSU_BANK"),
    ("BHARATFORG", "INE465A01025", "AUTO"),
    ("MAXHEALTH", "INE027H01010", "PHARMA"),
    ("NHPC", "INE848E01016", "POWER"),
]

# ── NEXT 100 (for NIFTY 200) ─────────────────────────────────────────
NEXT_100 = [
    ("ACC", "INE012A01025", "CEMENT"),
    ("ALKEM", "INE540L01014", "PHARMA"),
    ("ASTRAL", "INE006I01046", "CHEMICALS"),
    ("ATUL", "INE100A01010", "CHEMICALS"),
    ("AUBANK", "INE949L01017", "PRIVATE_BANK"),
    ("BALKRISIND", "INE787D01026", "AUTO"),
    ("BANDHANBNK", "INE545U01014", "PRIVATE_BANK"),
    ("BATAINDIA", "INE176A01028", "CONSUMER_DURABLES"),
    ("BEL", "INE263A01024", "INFRA"),
    ("BHEL", "INE257A01026", "INFRA"),
    ("BIOCON", "INE376G01013", "PHARMA"),
    ("CANFINHOME", "INE477A01020", "FINANCIALS"),
    ("CENTRALBK", "INE483A01010", "PSU_BANK"),
    ("COFORGE", "INE591G01017", "IT"),
    ("CONCOR", "INE111A01025", "INFRA"),
    ("CROMPTON", "INE299U01018", "CONSUMER_DURABLES"),
    ("CUMMINSIND", "INE298A01020", "INFRA"),
    ("DEEPAKFERT", "INE501A01019", "CHEMICALS"),
    ("DEEPAKNTR", "INE288B01029", "CHEMICALS"),
    ("DELHIVERY", "INE148O01028", "INFRA"),
    ("DIXON", "INE935N01020", "CONSUMER_DURABLES"),
    ("ESCORTS", "INE042A01014", "AUTO"),
    ("EXIDEIND", "INE302A01020", "AUTO"),
    ("FEDERALBNK", "INE171A01029", "PRIVATE_BANK"),
    ("FORTIS", "INE061F01013", "PHARMA"),
    ("GLENMARK", "INE935A01035", "PHARMA"),
    ("GMRINFRA", "INE776C01039", "INFRA"),
    ("GNFC", "INE113A01013", "CHEMICALS"),
    ("GODREJPROP", "INE484J01027", "REALTY"),
    ("GUJGASLTD", "INE844O01030", "ENERGY"),
    ("HINDPETRO", "INE094A01015", "ENERGY"),
    ("HONAUT", "INE671A01010", "CONSUMER_DURABLES"),
    ("IDFCFIRSTB", "INE092T01019", "PRIVATE_BANK"),
    ("IEX", "INE022Q01020", "FINANCIALS"),
    ("INDHOTEL", "INE053A01029", "CONSUMER_DURABLES"),
    ("INDIGO", "INE646L01027", "INFRA"),
    ("IPCALAB", "INE571A01020", "PHARMA"),
    ("IRFC", "INE053F01010", "FINANCIALS"),
    ("JKCEMENT", "INE823G01012", "CEMENT"),
    ("JUBLFOOD", "INE797F01012", "FMCG"),
    ("KPITTECH", "INE04I401011", "IT"),
    ("LAURUSLABS", "INE947Q01028", "PHARMA"),
    ("LICHSGFIN", "INE115A01026", "FINANCIALS"),
    ("LALPATHLAB", "INE600L01024", "PHARMA"),
    ("LTTS", "INE010V01017", "IT"),
    ("MFSL", "INE118H01025", "FINANCIALS"),
    ("MPHASIS", "INE356A01018", "IT"),
    ("MRF", "INE883A01011", "AUTO"),
    ("MUTHOOTFIN", "INE414G01012", "FINANCIALS"),
    ("NATIONALUM", "INE139A01034", "METALS"),
    ("NAVINFLUOR", "INE048G01026", "CHEMICALS"),
    ("NMDC", "INE584A01023", "METALS"),
    ("OBEROIRLTY", "INE093I01010", "REALTY"),
    ("OFSS", "INE881D01027", "IT"),
    ("PAGEIND", "INE761H01022", "TEXTILES"),
    ("PATANJALI", "INE349Z01014", "FMCG"),
    ("PERSISTENT", "INE262H01013", "IT"),
    ("PETRONET", "INE347G01014", "ENERGY"),
    ("PIIND", "INE603J01030", "CHEMICALS"),
    ("PRESTIGE", "INE811K01011", "REALTY"),
    ("RAMCOCEM", "INE331A01037", "CEMENT"),
    ("RAYMOND", "INE301A01014", "TEXTILES"),
    ("SAIL", "INE114A01011", "METALS"),
    ("SHREECEM", "INE070A01015", "CEMENT"),
    ("SONACOMS", "INE073K01018", "AUTO"),
    ("STARHEALTH", "INE575P01011", "INSURANCE"),
    ("SUMICHEM", "INE258G01010", "CHEMICALS"),
    ("SUNDARMFIN", "INE660A01013", "FINANCIALS"),
    ("SUNDRMFAST", "INE387A01021", "AUTO"),
    ("SUPREMEIND", "INE195A01028", "CHEMICALS"),
    ("SYNGENE", "INE398R01022", "PHARMA"),
    ("TATACHEM", "INE092A01019", "CHEMICALS"),
    ("TATACOMM", "INE151A01013", "TELECOM"),
    ("TATAELXSI", "INE670A01012", "IT"),
    ("THERMAX", "INE152A01029", "INFRA"),
    ("TIINDIA", "INE670A01012", "CONSUMER_DURABLES"),
    ("TIMKEN", "INE325A01013", "INFRA"),
    ("TORNTPOWER", "INE813H01021", "POWER"),
    ("TVSMOTOR", "INE494B01023", "AUTO"),
    ("UBL", "INE686F01025", "FMCG"),
    ("UCOBANK", "INE691A01018", "PSU_BANK"),
    ("UJJIVANSFB", "INE551W01018", "PRIVATE_BANK"),
    ("UNITDSPR", "INE854D01024", "FMCG"),
    ("UPL", "INE628A01036", "CHEMICALS"),
    ("VGUARD", "INE951I01027", "CONSUMER_DURABLES"),
    ("VINATIORGA", "INE410B01037", "CHEMICALS"),
    ("WHIRLPOOL", "INE716A01013", "CONSUMER_DURABLES"),
    ("YESBANK", "INE528G01035", "PRIVATE_BANK"),
    ("APLAPOLLO", "INE702C01027", "METALS"),
    ("AAVAS", "INE216P01012", "FINANCIALS"),
    ("ABSLAMC", "INE00RE01013", "FINANCIALS"),
    ("ABCAPITAL", "INE674K01013", "FINANCIALS"),
    ("AJANTPHARM", "INE031B01049", "PHARMA"),
    ("APLLTD", "INE901L01018", "PHARMA"),
    ("ASHOKLEY", "INE208A01029", "AUTO"),
    ("BAJAJELEC", "INE193E01025", "CONSUMER_DURABLES"),
    ("BANKINDIA", "INE084A01016", "PSU_BANK"),
    ("BLUESTARLT", "INE472A01039", "CONSUMER_DURABLES"),
    ("CARBORUNIV", "INE120A01034", "INFRA"),
    ("CGPOWER", "INE067A01029", "INFRA"),
]

# ── NEXT 300 (for NIFTY 500) — representative sample ─────────────────
# NOTE: This includes ~300 additional mid/small-cap stocks.
# ISINs should be verified against Upstox instrument master for production use.
NEXT_300 = [
    ("3MINDIA", "INE470A01017", "CONSUMER_DURABLES", "mid"),
    ("AARTIIND", "INE769A01020", "CHEMICALS", "mid"),
    ("ABFRL", "INE647O01011", "TEXTILES", "mid"),
    ("AEGISCHEM", "INE208C01025", "CHEMICALS", "mid"),
    ("AIAENG", "INE212H01026", "INFRA", "mid"),
    ("AFFLE", "INE00WF01019", "IT", "mid"),
    ("AKZOINDIA", "INE767A01016", "CHEMICALS", "mid"),
    ("AMARAJABAT", "INE885A01032", "AUTO", "mid"),
    ("ANGELONE", "INE732I01013", "FINANCIALS", "mid"),
    ("ANURAS", "INE457K01013", "FMCG", "mid"),
    ("ASTRAZEN", "INE203A01020", "PHARMA", "mid"),
    ("AVANTIFEED", "INE871C01038", "FMCG", "mid"),
    ("BASF", "INE172A01027", "CHEMICALS", "mid"),
    ("BAYERCROP", "INE462A01022", "CHEMICALS", "mid"),
    ("BIRLACORPN", "INE340A01012", "CEMENT", "mid"),
    ("BLUEDART", "INE233B01017", "INFRA", "mid"),
    ("BSOFT", "INE824B01021", "IT", "mid"),
    ("CAMPUS", "INE768G01024", "TEXTILES", "mid"),
    ("CASTROLIND", "INE172A01027", "ENERGY", "mid"),
    ("CDSL", "INE736A01011", "FINANCIALS", "mid"),
    ("CEATLTD", "INE482A01020", "AUTO", "mid"),
    ("CENTURYPLY", "INE281A01026", "CONSUMER_DURABLES", "mid"),
    ("CESC", "INE486A01021", "POWER", "mid"),
    ("CHAMBLFERT", "INE085A01013", "CHEMICALS", "mid"),
    ("CHEMPLASTS", "INE488B01017", "CHEMICALS", "mid"),
    ("CLEAN", "INE399L01023", "ENERGY", "mid"),
    ("COCHINSHIP", "INE704A01026", "INFRA", "mid"),
    ("CYIENT", "INE136B01020", "IT", "mid"),
    ("DATAPATTNS", "INE822P01016", "INFRA", "mid"),
    ("DCMSHRIRAM", "INE499A01024", "CHEMICALS", "mid"),
    ("DEVYANI", "INE872J01018", "FMCG", "mid"),
    ("DHANI", "INE680H01016", "FINANCIALS", "mid"),
    ("ECLERX", "INE738I01010", "IT", "mid"),
    ("ELECON", "INE205C01021", "INFRA", "mid"),
    ("EMAMILTD", "INE548C01032", "FMCG", "mid"),
    ("ENDURANCE", "INE913H01037", "AUTO", "mid"),
    ("ENGINERSIN", "INE510A01028", "INFRA", "mid"),
    ("EQUITASBNK", "INE063P01018", "PRIVATE_BANK", "mid"),
    ("ERIS", "INE406M01024", "PHARMA", "mid"),
    ("FINCABLES", "INE235A01022", "CONSUMER_DURABLES", "mid"),
    ("FINEORG", "INE686Y01026", "CHEMICALS", "mid"),
    ("FSL", "INE684F01012", "PHARMA", "mid"),
    ("GALAXYSURF", "INE600K01018", "CHEMICALS", "mid"),
    ("GARFIBRES", "INE276B01024", "CHEMICALS", "mid"),
    ("GILLETTE", "INE322A01010", "FMCG", "mid"),
    ("GLAXO", "INE159A01016", "PHARMA", "mid"),
    ("GLOBUSSPR", "INE950O01027", "FMCG", "mid"),
    ("GRANULES", "INE101D01020", "PHARMA", "mid"),
    ("GRINDWELL", "INE536A01023", "INFRA", "mid"),
    ("GSPL", "INE246F01010", "ENERGY", "mid"),
    ("GRSE", "INE382Z01011", "INFRA", "mid"),
    ("GSHIP", "INE517A01014", "INFRA", "mid"),
    ("HAPPSTMNDS", "INE419U01012", "IT", "mid"),
    ("HATSUN", "INE473B01035", "FMCG", "mid"),
    ("HFCL", "INE548A01028", "TELECOM", "mid"),
    ("HINDCOPPER", "INE531E01026", "METALS", "mid"),
    ("HINDZINC", "INE267A01025", "METALS", "mid"),
    ("HUDCO", "INE031A01017", "FINANCIALS", "mid"),
    ("ICRA", "INE725G01016", "FINANCIALS", "mid"),
    ("IDBI", "INE008A01015", "PSU_BANK", "mid"),
    ("IDFC", "INE043D01016", "FINANCIALS", "mid"),
    ("IIFL", "INE530B01024", "FINANCIALS", "mid"),
    ("IIFLWAM", "INE466M01020", "FINANCIALS", "mid"),
    ("INDIGOPNTS", "INE203B01028", "CHEMICALS", "mid"),
    ("INTELLECT", "INE306R01017", "IT", "mid"),
    ("ISGEC", "INE858B01011", "INFRA", "mid"),
    ("ISEC", "INE763G01038", "FINANCIALS", "mid"),
    ("JBCHEPHARM", "INE572A01028", "PHARMA", "mid"),
    ("JKLAKSHMI", "INE786A01032", "CEMENT", "mid"),
    ("JMFINANCIL", "INE780C01023", "FINANCIALS", "mid"),
    ("JSWINFRA", "INE880J01011", "INFRA", "mid"),
    ("JUBLINGREA", "INE040A01034", "CONSUMER_DURABLES", "mid"),
    ("JUSTDIAL", "INE599M01018", "IT", "mid"),
    ("JYOTHYLAB", "INE668F01031", "FMCG", "mid"),
    ("KAJARIACER", "INE217B01036", "CONSUMER_DURABLES", "mid"),
    ("KALPATPOWR", "INE220B01022", "INFRA", "mid"),
    ("KALYANKJIL", "INE303R01014", "CONSUMER_DURABLES", "mid"),
    ("KEC", "INE389H01022", "INFRA", "mid"),
    ("KEI", "INE878B01027", "INFRA", "mid"),
    ("KIRLOSENG", "INE146L01010", "INFRA", "mid"),
    ("KNRCON", "INE634I01029", "INFRA", "mid"),
    ("KPRMILL", "INE930H01023", "TEXTILES", "mid"),
    ("KRBL", "INE001B01026", "FMCG", "mid"),
    ("KTKBANK", "INE614B01018", "PRIVATE_BANK", "mid"),
    ("L&TFH", "INE498A01024", "FINANCIALS", "mid"),
    ("LATENTVIEW", "INE0CCK01010", "IT", "mid"),
    ("LAXMIMACH", "INE234B01023", "INFRA", "mid"),
    ("LINDEINDIA", "INE473A01011", "CHEMICALS", "mid"),
    ("LUXIND", "INE150G01020", "TEXTILES", "mid"),
    ("MAHSEAMLES", "INE271B01025", "METALS", "mid"),
    ("MANAPPURAM", "INE522D01027", "FINANCIALS", "mid"),
    ("MASTEK", "INE759A01021", "IT", "mid"),
    ("MCX", "INE745G01035", "FINANCIALS", "mid"),
    ("METROPOLIS", "INE112L01020", "PHARMA", "mid"),
    ("MINDAIND", "INE405E01023", "AUTO", "mid"),
    ("MOTILALOFS", "INE338I01027", "FINANCIALS", "mid"),
    ("MRPL", "INE196A01026", "ENERGY", "mid"),
    ("NATCOPHARM", "INE987B01026", "PHARMA", "mid"),
    ("NIACL", "INE047B01011", "INSURANCE", "mid"),
    ("NLC", "INE589A01014", "POWER", "mid"),
    ("NOCIL", "INE163A01018", "CHEMICALS", "mid"),
    ("OLECTRA", "INE260D01016", "AUTO", "mid"),
    ("ORIENTELEC", "INE142Z01019", "CONSUMER_DURABLES", "mid"),
    ("PGHH", "INE179A01014", "FMCG", "mid"),
    ("PHOENIXLTD", "INE211B01039", "REALTY", "mid"),
    ("POLYMED", "INE205C01021", "PHARMA", "mid"),
    ("POONAWALLA", "INE511C01022", "FINANCIALS", "mid"),
    ("POWERMECH", "INE775I01014", "INFRA", "mid"),
    ("PRINCEPIPE", "INE689W01016", "CHEMICALS", "mid"),
    ("PRSMJOHNSN", "INE068I01014", "CONSUMER_DURABLES", "mid"),
    ("PSB", "INE608A01012", "PSU_BANK", "mid"),
    ("QUESS", "INE615P01015", "IT", "mid"),
    ("RADICO", "INE944F01028", "FMCG", "mid"),
    ("RAIN", "INE855B01025", "CHEMICALS", "mid"),
    ("RAJESHEXPO", "INE343B01030", "FMCG", "mid"),
    ("RALLIS", "INE613A01020", "CHEMICALS", "mid"),
    ("RKFORGE", "INE399G01023", "AUTO", "mid"),
    ("ROUTE", "INE450U01017", "IT", "mid"),
    ("RVNL", "INE415G01027", "INFRA", "mid"),
    ("SAREGAMA", "INE979A01025", "MEDIA", "mid"),
    ("SCHAEFFLER", "INE513A01014", "AUTO", "mid"),
    ("SHOPERSTOP", "INE498B01024", "CONSUMER_DURABLES", "small"),
    ("SJVN", "INE002L01015", "POWER", "mid"),
    ("SKFINDIA", "INE640A01023", "AUTO", "mid"),
    ("SOBHA", "INE671H01015", "REALTY", "mid"),
    ("SOLARA", "INE624Z01016", "PHARMA", "mid"),
    ("SPARC", "INE232I01014", "PHARMA", "mid"),
    ("STLTECH", "INE089C01029", "TELECOM", "mid"),
    ("SUNTV", "INE424H01027", "MEDIA", "mid"),
    ("SUPRAJIT", "INE399C01030", "AUTO", "mid"),
    ("SUVENPHAR", "INE03QN01013", "PHARMA", "mid"),
    ("SWANENERGY", "INE665A01038", "ENERGY", "mid"),
    ("SYMPHONY", "INE225D01027", "CONSUMER_DURABLES", "mid"),
    ("TANLA", "INE483C01032", "IT", "mid"),
    ("TATAINVEST", "INE672A01018", "FINANCIALS", "mid"),
    ("TATAMETALI", "INE056C01010", "METALS", "mid"),
    ("TEAMLEASE", "INE985S01024", "IT", "mid"),
    ("TECHNO", "INE345L01018", "INFRA", "mid"),
    ("THYROCARE", "INE594H01019", "PHARMA", "mid"),
    ("TINPLATE", "INE422A01016", "METALS", "mid"),
    ("TMB", "INE700A01033", "PRIVATE_BANK", "mid"),
    ("TRIDENT", "INE064C01022", "TEXTILES", "mid"),
    ("TRITURBINE", "INE152M01016", "POWER", "mid"),
    ("TRIVENI", "INE256C01024", "INFRA", "mid"),
    ("TTML", "INE037A01022", "TELECOM", "mid"),
    ("TV18BRDCST", "INE886H01027", "MEDIA", "mid"),
    ("UTIAMC", "INE094J01016", "FINANCIALS", "mid"),
    ("VAIBHAVGBL", "INE884A01027", "CONSUMER_DURABLES", "mid"),
    ("VARDHMNSTX", "INE825A01012", "TEXTILES", "mid"),
    ("VBLLTD", "INE200M01013", "FMCG", "mid"),
    ("VIPIND", "INE054A01027", "CONSUMER_DURABLES", "mid"),
    ("VMART", "INE665J01013", "CONSUMER_DURABLES", "small"),
    ("VSTIND", "INE710A01016", "FMCG", "mid"),
    ("WELCORP", "INE338B01022", "METALS", "mid"),
    ("WELSPUNLIV", "INE192B01031", "TEXTILES", "mid"),
    ("WESTLIFE", "INE274F01020", "FMCG", "mid"),
    ("WOCKPHARMA", "INE049B01025", "PHARMA", "mid"),
    ("ZEEL", "INE256A01028", "MEDIA", "mid"),
    ("ZENSARTECH", "INE520A01027", "IT", "mid"),
    ("ZPFIN", "INE251B01019", "FINANCIALS", "mid"),
    ("ABSLAMC", "INE00RE01013", "FINANCIALS", "mid"),
    ("AETHER", "INE0GXQ01019", "CHEMICALS", "mid"),
    ("ALKYLAMINE", "INE150B01039", "CHEMICALS", "mid"),
    ("ANANDRATHI", "INE657B01018", "FINANCIALS", "mid"),
    ("ANANTRAJ", "INE242C01024", "REALTY", "mid"),
    ("APTUS", "INE852O01025", "FINANCIALS", "mid"),
    ("ASTER", "INE914M01019", "PHARMA", "mid"),
    ("BANARISUG", "INE459A01010", "FMCG", "mid"),
    ("BEML", "INE258A01016", "INFRA", "mid"),
    ("BLS", "INE153T01027", "INFRA", "mid"),
    ("BRIGADE", "INE791I01019", "REALTY", "mid"),
    ("BSE", "INE118H01025", "FINANCIALS", "mid"),
    ("CAPLIPOINT", "INE475E01026", "PHARMA", "mid"),
    ("CERA", "INE739E01017", "CONSUMER_DURABLES", "mid"),
    ("CHALET", "INE427F01016", "CONSUMER_DURABLES", "mid"),
    ("CUB", "INE112A01023", "PRIVATE_BANK", "mid"),
    ("CRISIL", "INE007A01025", "FINANCIALS", "mid"),
    ("DALBHARAT", "INE453B01029", "CEMENT", "mid"),
    ("DMART", "INE192R01011", "FMCG", "mid"),
    ("DOMS", "INE345W01019", "CONSUMER_DURABLES", "mid"),
    ("EICHERMOT", "INE066A01029", "AUTO", "mid"),
    ("EIH", "INE230A01023", "CONSUMER_DURABLES", "mid"),
    ("EPL", "INE255C01020", "CONSUMER_DURABLES", "mid"),
    ("FACT", "INE188A01015", "CHEMICALS", "mid"),
    ("FINOLEX", "INE235A01022", "CONSUMER_DURABLES", "mid"),
    ("FIRSTSOUR", "INE684F01012", "IT", "mid"),
    ("FLUOROCHEM", "INE580B01039", "CHEMICALS", "mid"),
    ("GHCL", "INE539A01019", "CHEMICALS", "mid"),
    ("GLAXO", "INE159A01016", "PHARMA", "mid"),
    ("GODFRYPHLP", "INE260B01028", "FMCG", "mid"),
    ("GPIL", "INE635Q01024", "METALS", "mid"),
    ("GUJALKALI", "INE186A01019", "CHEMICALS", "mid"),
    ("HECLTD", "INE549A01026", "INFRA", "mid"),
    ("HEMIPROP", "INE373I01016", "REALTY", "mid"),
    ("HGINFRA", "INE545H01022", "INFRA", "mid"),
    ("HOMEFIRST", "INE481N01020", "FINANCIALS", "mid"),
    ("IBREALEST", "INE069I01010", "REALTY", "mid"),
    ("IFBIND", "INE559A01017", "CONSUMER_DURABLES", "small"),
    ("IIFLSEC", "INE530B01040", "FINANCIALS", "small"),
    ("INOXWIND", "INE066P01011", "POWER", "mid"),
    ("IONEXCHANG", "INE503A01015", "CHEMICALS", "mid"),
    ("JAMNAAUTO", "INE039C01032", "AUTO", "mid"),
    ("JBMA", "INE227A01013", "AUTO", "mid"),
    ("JINDALSAW", "INE324A01024", "METALS", "mid"),
    ("JKPAPER", "INE789E01012", "CONSUMER_DURABLES", "mid"),
    ("JPASSOCIAT", "INE455F01025", "INFRA", "small"),
    ("JSWHL", "INE824A01015", "METALS", "mid"),
    ("JUBILANT", "INE700A01033", "PHARMA", "mid"),
    ("KARURVYSYA", "INE036D01028", "PRIVATE_BANK", "mid"),
    ("KAYNES", "INE918Z01010", "IT", "mid"),
    ("KFINTECH", "INE138Y01010", "IT", "mid"),
    ("KOLTEPATIL", "INE094I01018", "REALTY", "mid"),
    ("KOPRAN", "INE082A01010", "PHARMA", "small"),
    ("KRSNAA", "INE328Q01018", "PHARMA", "mid"),
    ("LXCHEM", "INE576O01020", "CHEMICALS", "mid"),
    ("MAHABANK", "INE457A01014", "PSU_BANK", "mid"),
    ("MAHINDCIE", "INE536H01013", "AUTO", "mid"),
    ("MAHLOG", "INE766P01016", "INFRA", "mid"),
    ("MAITHANALL", "INE683B01019", "CHEMICALS", "mid"),
    ("MAPMYINDIA", "INE353V01018", "IT", "mid"),
    ("MASFIN", "INE150P01013", "FINANCIALS", "mid"),
    ("MAYURUNIQ", "INE885E01018", "TEXTILES", "small"),
    ("MEDANTA", "INE546S01023", "PHARMA", "mid"),
    ("MIDHANI", "INE099Z01011", "METALS", "mid"),
    ("MMTC", "INE123F01029", "METALS", "mid"),
    ("MOLDTKPAC", "INE468B01015", "CONSUMER_DURABLES", "mid"),
    ("MOIL", "INE490G01020", "METALS", "mid"),
    ("MTARTECH", "INE864P01027", "INFRA", "mid"),
    ("NAZARA", "INE418L01014", "IT", "mid"),
    ("NESCO", "INE317F01035", "REALTY", "mid"),
    ("NETWORK18", "INE870H01016", "MEDIA", "mid"),
    ("NEWGEN", "INE877F01012", "IT", "mid"),
    ("NUVAMA", "INE531B01024", "FINANCIALS", "mid"),
    ("EIDPARRY", "INE126A01031", "FMCG", "mid"),
    ("PCBL", "INE602A01023", "CHEMICALS", "mid"),
    ("PDSL", "INE111Q01018", "IT", "mid"),
    ("PGEL", "INE538B01015", "CHEMICALS", "mid"),
    ("PNBHOUSING", "INE572E01012", "FINANCIALS", "mid"),
    ("PPLPHARMA", "INE214C01015", "PHARMA", "mid"),
    ("PRISM", "INE010A01011", "CEMENT", "mid"),
    ("PVRINOX", "INE191H01014", "MEDIA", "mid"),
    ("RBLBANK", "INE976G01028", "PRIVATE_BANK", "mid"),
    ("REDINGTON", "INE891D01026", "IT", "mid"),
    ("RENUKA", "INE087H01022", "FMCG", "mid"),
    ("RHI", "INE743B01012", "INFRA", "mid"),
    ("RITES", "INE320J01015", "INFRA", "mid"),
    ("ROSSARI", "INE02A801020", "CHEMICALS", "mid"),
    ("SAFARI", "INE180C01018", "CONSUMER_DURABLES", "mid"),
    ("SANOFI", "INE058A01010", "PHARMA", "mid"),
    ("SAPPHIRE", "INE829A01014", "TEXTILES", "mid"),
    ("SHILPAMED", "INE790G01031", "PHARMA", "mid"),
    ("SOLARINDS", "INE343H01029", "INFRA", "mid"),
    ("SOUTHBANK", "INE683A01023", "PRIVATE_BANK", "mid"),
    ("SPARC", "INE232I01014", "PHARMA", "mid"),
    ("STAR", "INE092J01027", "CONSUMER_DURABLES", "mid"),
    ("STYRENIX", "INE489A01015", "CHEMICALS", "mid"),
    ("SUDARSCHEM", "INE659B01023", "CHEMICALS", "mid"),
    ("SUNTECK", "INE805D01034", "REALTY", "mid"),
    ("SWSOLAR", "INE00G001012", "POWER", "mid"),
    ("TARSONS", "INE138M01015", "PHARMA", "mid"),
    ("TATVA", "INE819C01013", "PHARMA", "mid"),
    ("TDPOWER", "INE419M01027", "POWER", "mid"),
    ("TI", "INE149A01033", "METALS", "mid"),
    ("TITAGARH", "INE615H01020", "INFRA", "mid"),
    ("TREASURYCE", "INE070I01018", "CONSUMER_DURABLES", "mid"),
    ("UNICHEMLAB", "INE351A01035", "PHARMA", "mid"),
    ("USHAMART", "INE228A01035", "AUTO", "mid"),
    ("UTKARSHBNK", "INE482E01022", "PRIVATE_BANK", "mid"),
    ("VARROC", "INE665L01035", "AUTO", "mid"),
    ("VENKEYS", "INE398A01010", "FMCG", "mid"),
    ("VOLTAMP", "INE540H01012", "CONSUMER_DURABLES", "mid"),
    ("WABAG", "INE274B01020", "INFRA", "mid"),
    ("WATERBASE", "INE054C01016", "FMCG", "small"),
    ("WELENT", "INE428A01036", "POWER", "mid"),
    ("WHEELS", "INE029A01011", "AUTO", "mid"),
    ("WONDERLA", "INE066V01014", "CONSUMER_DURABLES", "mid"),
    ("ZENITHSTL", "INE034A01015", "METALS", "mid"),
    ("ZENTEC", "INE251B01027", "IT", "mid"),
]


def build_tier(tier_name, extra_stocks, parent_data, tiers_list):
    """Build a tier by merging parent data with extra stocks."""
    data = dict(parent_data)  # copy parent

    for item in extra_stocks:
        if len(item) == 3:
            sym, isin, sector = item
            cap = "large"
        else:
            sym, isin, sector, cap = item

        # Update index membership for existing entries
        # or create new entry
        if sym in data:
            if tier_name not in data[sym]["index_membership"]:
                data[sym]["index_membership"].append(tier_name)
            continue

        data[sym] = make_entry(sym, isin, sector, list(tiers_list), cap)

    return data


def main():
    # Load NIFTY 50 base
    with open(os.path.join(SYMBOLS_DIR, "nifty50.json")) as f:
        nifty50 = json.load(f)

    print(f"NIFTY 50: {len(nifty50)} symbols")

    # Build NIFTY 100
    nifty100 = build_tier("NIFTY100", NEXT_50, nifty50, ["NIFTY100", "NIFTY200", "NIFTY500"])
    # Update NIFTY 50 members to include NIFTY100 membership
    for sym in nifty50:
        if "NIFTY100" not in nifty100[sym]["index_membership"]:
            nifty100[sym]["index_membership"].append("NIFTY100")
    print(f"NIFTY 100: {len(nifty100)} symbols")

    # Build NIFTY 200
    nifty200 = build_tier("NIFTY200", NEXT_100, nifty100, ["NIFTY200", "NIFTY500"])
    for sym in nifty100:
        if "NIFTY200" not in nifty200[sym]["index_membership"]:
            nifty200[sym]["index_membership"].append("NIFTY200")
    print(f"NIFTY 200: {len(nifty200)} symbols")

    # Build NIFTY 500
    nifty500 = build_tier("NIFTY500", NEXT_300, nifty200, ["NIFTY500"])
    for sym in nifty200:
        if "NIFTY500" not in nifty500[sym]["index_membership"]:
            nifty500[sym]["index_membership"].append("NIFTY500")
    print(f"NIFTY 500: {len(nifty500)} symbols")

    # Write output files
    for name, data in [
        ("nifty100", nifty100),
        ("nifty200", nifty200),
        ("nifty500", nifty500),
    ]:
        path = os.path.join(SYMBOLS_DIR, f"{name}.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  Written: {path} ({len(data)} symbols)")

    # Print sector distribution
    print("\nSector distribution (NIFTY 500):")
    sectors = {}
    for _sym, meta in nifty500.items():
        s = meta["sector_id"]
        sectors[s] = sectors.get(s, 0) + 1
    for s, c in sorted(sectors.items(), key=lambda x: -x[1]):
        print(f"  {s:20s} {c:4d}")


if __name__ == "__main__":
    main()
