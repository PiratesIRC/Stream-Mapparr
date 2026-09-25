"""Built-in US channel alias table for Stream-Mapparr (Phase 1).

Maps canonical channel names (the matcher query) to known IPTV stream-name
variants. Used by FuzzyMatcher.alias_lookup for exact-normalized matching.

GUARDS (hard-won in the sibling Lineuparr plugin):
- Do NOT use bare short tokens (UP, GET, GAC, great) as alias VALUES — they
  normalize to 2-3 chars and exact-match unrelated short streams. The canonical
  name carries the alias instead.
- Do NOT catch-all regional feeds (e.g. FanDuel regionals) to a generic parent.
- Country-specific variants belong in COUNTRY_ALIASES (Phase 2), never here
  (cross-market leak, see Lineuparr bug-063).
"""

CHANNEL_ALIASES = {
    # --- News ---
    "ABC News Live": ["ABC News", "ABC News Live"],
    "AccuWeather": ["AccuWeather", "Accu Weather"],
    "BBC News": ["BBC News", "BBC World News"],
    "Bloomberg TV": ["Bloomberg", "Bloomberg Television", "Bloomberg TV"],
    "Bloomberg Television": ["Bloomberg", "Bloomberg TV", "Bloomberg Television"],
    "CNN": ["CNN", "CNN US", "CNN USA"],
    "CNN En Español": ["CNN Espanol", "CNN en Espanol", "CNN Spanish"],
    "CNNi": ["CNN International", "CNNi"],
    "CNBC": ["CNBC", "CNBC US"],
    "CNBC World": ["CNBC World"],
    "C-SPAN": ["C-SPAN", "CSPAN", "C SPAN"],
    "C-SPAN2": ["C-SPAN 2", "CSPAN 2", "C SPAN 2", "C-SPAN2"],
    "FOX Business Network": ["Fox Business", "FBN", "FOX Business"],
    "FOX News Channel": ["Fox News", "FNC", "FOX NEWS", "Fox News Channel"],
    "E! Entertainment Television": ["E!", "E Entertainment", "E! Entertainment", "E! Entertainment Television"],
    "FOX Weather": ["Fox Weather"],
    "HLN": ["HLN", "Headline News", "Headline News Network", "HLN Headline News", "HLN Headline News Network", "CNN HLN", "CNN Headline News"],
    "HLN Headline News Network": ["HLN", "Headline News", "Headline News Network", "CNN HLN", "CNN Headline News"],
    "MS Now": ["MSNBC", "MSNBC Now", "MS Now"],
    "MSNBC": ["MSNBC", "MS Now", "MSNBC Now"],
    "Newsmax": ["Newsmax", "Newsmax TV"],
    "NewsNation": ["NewsNation", "News Nation", "WGN America", "WGN"],
    "Weather Channel": ["Weather Channel", "TWC", "The Weather Channel"],

    # --- Sports ---
    "ACC Network": ["ACC Network", "ACCN"],
    "Big Ten Network": ["Big Ten Network", "BTN", "Big 10 Network", "Big Ten"],
    "Big 10 Network": ["Big Ten Network", "BTN", "Big 10 Network", "Big Ten"],
    "CBS Sports Network": ["CBS Sports Network", "CBSSN", "CBS Sports"],
    "ESPN": ["ESPN", "ESPN US", "ESPN USA"],
    "ESPN2": ["ESPN 2", "ESPN2"],
    "ESPNEWS": ["ESPN News", "ESPNEWS", "ESPNews"],
    "ESPNU": ["ESPNU"],
    "FanDuel TV": ["FanDuel TV", "FanDuel", "TVG"],
    # NOTE: Do NOT alias regional FanDuel Sports feeds (Cincinnati, Detroit,
    # Florida, Midwest, North, Ohio, Oklahoma, SoCal, South, Southeast,
    # Southwest, Sun, West, Wisconsin) to "FanDuel TV Extra". That fallback
    # gave every regional sports channel the same generic FanDuel EPG when
    # no regional EPG existed - worse than NO MATCH. Regions with a real
    # regional EPG (e.g. Midwest, Southwest, West) match by direct name.
    "FS1": ["Fox Sports 1", "FS1", "FS 1", "Fox Sport 1"],
    "Fox Sports 1": ["Fox Sports 1", "FS1", "FS 1", "Fox Sport 1"],
    "FS2": ["Fox Sports 2", "FS2", "FS 2", "Fox Sport 2"],
    "Fox Sports 2": ["Fox Sports 2", "FS2", "FS 2", "Fox Sport 2"],
    "FOX Sports": ["FOX Sports", "Fox Sports"],
    "GOLF Channel": ["Golf Channel", "Golf Ch", "GOLF", "NBC Golf Channel", "NBC GOLF", "US GOLF"],
    "Golf Channel": ["Golf Channel", "Golf Ch", "GOLF", "NBC Golf Channel", "NBC GOLF", "US GOLF"],
    "MLB Network": ["MLB Network", "MLB Net", "MLBN"],
    "NBA TV": ["NBA TV", "NBATV"],
    "NFL Network": ["NFL Network", "NFL Net", "NFLN"],
    "NHL Network": ["NHL Network", "NHL Net", "NHLN"],
    "SEC Network": ["SEC Network", "SECN"],
    "Tennis Channel HD": ["Tennis Channel", "Tennis Ch"],
    "TUDN": ["TUDN", "Univision Deportes", "Univision Deportes Network", "UDN"],
    "Univision Deportes": ["TUDN", "Univision Deportes", "Univision Deportes Network"],

    # --- Movies ---
    "Cinemax": ["Cinemax", "Cinemax US"],
    # FXM is the former Fox Movie Channel; fully rebranded FX Movie Channel (FXM)
    # in 2013. The classic-films block carries the "FXM Retro" name.
    "FXM": ["FXM", "FX Movie Channel", "FXMovie", "Fox Movie Channel", "FXM Retro"],
    "FX Movie Channel": ["FXM", "FX Movie Channel", "FXMovie", "Fox Movie Channel", "FXM Retro"],
    "HBO East": ["HBO East", "HBO (East)", "HBO"],
    "HBO Comedy East HD": ["HBO Comedy East", "HBO Comedy (East)", "HBO Comedy"],
    "HBO Drama HD East": ["HBO Drama East", "HBO Drama (East)", "HBO Drama", "HBO Signature"],
    "HBO Hits HD East": ["HBO Hits East", "HBO Hits (East)", "HBO Hits", "HBO 2", "HBO2"],
    "HBO Latino": ["HBO Latino"],
    "HBO Movies HD": ["HBO Movies", "HBO Movies HD", "HBO Movies East", "HBO Movies (East)", "HBO Zone"],
    "Paramount+ with SHOWTIME EAST": ["Showtime East", "Showtime (East)", "SHOWTIME EAST", "Showtime"],
    "Showtime (E)": ["Paramount+ with Showtime", "Paramount+ with Showtime HD", "Showtime East", "Showtime"],
    "Showtime (W)": ["Paramount+ with Showtime (Pacific)", "Paramount+ with Showtime HD (Pacific)", "Showtime West"],
    "Showtime 2": ["Showtime 2 East", "Showtime 2 (East)", "Showtime 2", "SHOWTIME 2"],
    "SHOWTIME 2 East": ["Showtime 2 East", "Showtime 2 (East)", "Showtime 2"],
    "STARZ Cinema East HD": ["Starz Cinema East", "STARZ CINEMA EAST"],
    "STARZ Comedy East HD": ["Starz Comedy East", "STARZ COMEDY EAST"],
    "STARZ Edge East HD": ["Starz Edge East", "STARZ EDGE EAST"],
    "STARZ ENCORE East": ["Starz Encore East", "STARZ ENCORE EAST", "Starz Encore"],
    "STARZ ENCORE West": ["Starz Encore West", "STARZ ENCORE WEST"],
    "STARZ ENCORE Westerns": ["Starz Encore Westerns", "STARZ ENCORE WESTERNS", "StarzenCore Westerns"],
    "STARZ In Black East HD": ["Starz In Black East", "STARZ IN BLACK EAST"],
    "SHOWTIME EXTREME": ["Showtime Extreme", "SHO Extreme"],
    "STARZ": ["Starz", "STARZ"],
    "STARZ Kids & Family": ["Starz Kids", "Starz Kids HD", "STARZ Kids & Family"],
    "SundanceTV": ["Sundance", "SundanceTV", "Sundance TV"],
    "TCM": ["TCM", "Turner Classic Movies"],

    # --- Kids ---
    "Cartoon Network": ["Cartoon Network", "Cartoon Network HD", "CN", "Cartoon Net HD", "Cartoon Netwrk"],
    "Cartoon Network East": ["Cartoon Network", "Cartoon Network East", "CN"],
    "Disney Channel East": ["Disney Channel", "Disney Channel East", "Disney Ch"],
    "Disney Junior": ["Disney Junior", "Disney Jr"],
    "Disney Jr HD": ["Disney Junior HD", "Disney Junior", "Disney Jr"],
    "Nick Jr.": ["Nick Jr", "Nick Junior"],
    "Nickelodeon": ["Nickelodeon", "Nickelodeon East", "Nick", "Nick at Nite"],
    "Nick/Nick at Nite (W)": ["Nickelodeon West", "Nick West", "Nick at Nite West"],
    "Nickelodeon East": ["Nickelodeon", "Nickelodeon East", "Nick", "Nickelodeon US"],

    # --- Entertainment ---
    "A&E": ["A&E", "A and E", "AE"],
    "AMC": ["AMC", "AMC US"],
    "BBC America": ["BBC America", "BBCA"],
    "CleoTV": ["Cleo TV", "CleoTV"],
    "BET": ["BET", "Black Entertainment Television"],
    "E!": ["E!", "E Entertainment", "E! Entertainment", "E! Entertainment Television"],
    "Freeform": ["Freeform", "ABC Family"],
    "FX": ["FX", "FX US"],
    "FXX": ["FXX", "FX X"],
    "HISTORY Channel, The": ["History", "History Channel", "HISTORY"],
    "Heroes & Icons (H&I)": ["Heroes & Icons", "Heroes and Icons", "H&I", "Heros & Icons"],
    "ION East HD": ["ION", "ION East", "ION Television"],
    "Lifetime": ["Lifetime", "Lifetime US"],
    "LMN": ["LMN", "Lifetime Movie Network", "LMN HD"],
    "Lifetime Movie Network": ["LMN", "Lifetime Movie Network", "LMN HD"],
    "Paramount Network": ["Paramount", "Paramount Network"],
    "Syfy": ["Syfy", "Sci-Fi", "SciFi"],
    "TBS": ["TBS", "TBS US"],
    "TNT": ["TNT", "TNT US", "TNT USA"],
    "ShortsTV": ["Shorts TV", "ShortsTV"],
    "USA Network": ["USA Network", "USA"],
    "UPTV": ["UP TV", "UPTV"],
    "truTV": ["truTV", "tru TV"],

    # --- Home & Garden ---
    "DIY": ["DIY Network", "Magnolia Network", "Magnolia"],

    # --- Reality & Lifestyle ---
    "Bravo": ["Bravo", "Bravo US"],
    "Bravo Vault": ["Bravo Vault"],
    "HGTV": ["HGTV", "HGTV US", "Home & Garden Television", "Home and Garden Television"],
    "OWN": ["OWN", "Oprah Winfrey Network"],
    "OWN: Oprah Winfrey Network": ["OWN", "Oprah Winfrey Network"],
    "TLC": ["TLC", "TLC US"],

    # --- Comedy ---
    "Comedy Central": ["Comedy Central", "CC", "ComedyCentral", "ComedyCentHD", "Comedy Central HD"],

    # --- Discovery ---
    "Animal Planet": ["Animal Planet", "Animal Planet US"],
    "Discovery": ["Discovery", "Discovery Channel"],
    "Investigation Discovery": ["Investigation Discovery", "ID"],
    "National Geographic": ["National Geographic", "National Geographic HD", "Nat Geo", "Nat Geo HD", "NatGeo"],
    "National Geographic Channel": ["Nat Geo", "National Geographic", "NatGeo"],
    "Nat Geo WILD": ["Nat Geo Wild", "NatGeo Wild", "National Geographic Wild"],
    "Nat Geo Wild": ["Nat Geo Wild", "NatGeo Wild", "National Geographic Wild"],
    "Science": ["Discovery Science", "Science Channel"],
    "Smithsonian Channel": ["Smithsonian", "Smithsonian Channel"],

    # --- Crime ---
    "Oxygen": ["Oxygen True Crime", "Oxygen True Crime HD"],
    "Oxygen True Crime": ["Oxygen", "Oxygen True Crime"],
    "Oxygen True Crime Archives": ["Oxygen True Crime Archives"],
    # Justice Network relaunched as True Crime Network in 2020; "Justice
    # Central.TV" was its companion streaming brand. Match all three names.
    "Justice Network": ["Justice Network", "Justice Central.TV", "Justice Central TV", "Justice Central", "True Crime Network"],
    "True Crime Network": ["True Crime Network", "Justice Network", "Justice Central.TV", "Justice Central TV", "Justice Central"],
    "Justice Central.TV": ["Justice Central.TV", "Justice Central TV", "Justice Central", "Justice Network", "True Crime Network"],

    # --- Music ---
    "CMT": ["CMT", "Country Music Television"],
    "MTV": ["MTV", "MTV US", "MTV - Music Television", "MTV Music Television"],
    "MTV2": ["MTV2", "MTV 2", "MTV2: Music Television", "MTV2: Music Television HD"],
    "VH1": ["VH1", "VH 1"],

    # --- Food & Travel ---
    "Cooking Channel": ["Cooking Channel", "Cooking Ch"],
    "Food Network": ["Food Network", "Food Net"],
    "Recipe TV": ["RecipeTV", "Recipe TV"],
    "Tastemade Home": ["Tastemade"],
    "Tastemade Travel": ["Tastemade Travel"],

    # --- Premium channels ---
    "EPIX 1": ["EPIX", "Epix", "EPIX 1", "MGM+", "MGM+ East", "MGM+ HD"],
    "EPIX 2": ["EPIX 2", "MGM 2", "MGM+ 2"],
    "EPIX Hits": ["EPIX Hits", "MGM+ Hits", "MGM Hits"],
    "EPIX Drive-In": ["EPIX Drive-In", "MGM+ Drive-In", "MGM Drive-In"],
    "The Movie Channel (E)": ["The Movie Channel", "Movie Channel East", "TMC", "TMC East"],
    "The Movie Channel (W)": ["The Movie Channel West", "Movie Channel West", "TMC West"],
    "The Movie Channel Xtra": ["TMC Xtra", "Movie Channel Xtra", "The Movie Channel Extra"],
    "The Movie Channel Xtra (E)": ["TMC Xtra", "Movie Channel Xtra", "The Movie Channel Extra East"],

    # --- Additional aliases for DISH lineup ---
    "American Heroes Channel": ["AHC", "American Heroes Channel", "American Heroes"],
    "BabyFirstTV": ["Baby First", "BabyFirst", "BabyFirstTV", "Baby First TV"],
    # getTV is owned by Sony; rebranded to "Great Entertainment Television" in
    # 2023. Match the old and new branding. Bare "GET"/"get."/"great." are NOT
    # used as alias VALUES: they normalize to "get"/"great" and would force a
    # false score-100 match against any stream that happens to normalize the
    # same way. "Get TV"/"getTV"/"GETTV" all normalize to "gettv" (unambiguous).
    "getTV": ["Get TV", "getTV", "GETTV", "Great Entertainment Television"],
    "GET": ["getTV", "Get TV", "GETTV", "Great Entertainment Television"],
    "Great Entertainment Television": ["getTV", "Get TV", "GETTV", "Great Entertainment Television"],
    "GSN": ["GSN", "Game Show Network"],
    "Pop": ["Pop TV", "Pop TV East"],
    "ReelzChannel": ["Reelz", "ReelzChannel"],
    "Telemundo": ["Telemundo"],

    # --- Faith & Family ---
    "FETV": ["FETV", "Family Entertainment Television", "Family Entertainment TV"],

    # --- Other ---
    "MeTV": ["ME TV", "MeTV"],
    "Mythbusters": ["Mythbusters", "MYTHBUSTERS"],
    "Hallmark Channel": ["Hallmark", "Hallmark Channel"],
    "Hallmark Mystery": ["Hallmark Movies", "Hallmark Mystery", "Hallmark Movies & Mysteries"],
    "Hallmark Movies & Mysteries": ["Hallmark Mystery", "Hallmark Movies & More", "Hallmark Mystery HD"],
    "MotorTrend": ["MotorTrend", "Motor Trend", "Velocity"],
    "Travel Channel": ["Travel Channel", "Travel Ch"],

    # --- Faith (rebrands) ---
    # Hillsong Channel rebranded to TBN Inspire on 2022-01-01 in the US.
    "Hillsong Channel": ["Hillsong Channel", "TBN Inspire", "Hillsong", "The Church Channel"],

    # --- Movies (rebrands / discontinued) ---
    # Showtime Beyond was rebranded SHO×BET on 2020-07-15.
    "Showtime Beyond": ["Showtime Beyond", "SHO×BET", "SHO BET", "SHOxBET", "Showtime BET", "SHO X BET"],

    # --- US rebrands (old broadcast names <-> current names) ----------------
    # Provider lineups were captured at different times, so a channel may carry
    # either its pre- or post-rebrand name. These aliases let a lineup channel
    # match streams/EPG under EITHER name. US_Combined has been de-duped so the
    # old and new names never coexist there; per-provider lineups (DISH/Verizon)
    # keep whatever name the provider used, hence both directions are covered.
    # NOTE: deliberately NOT aliasing regional Bally/FanDuel/Fox Sports Net
    # feeds (see the FanDuel TV note above) - that catch-all was harmful.
    #
    # Sports
    "SportsNet Pittsburgh": ["SportsNet Pittsburgh", "AT&T SportsNet Pittsburgh", "ATT SportsNet Pittsburgh", "Root Sports Pittsburgh"],
    # Premium movies - current names also matching the old multiplex names
    "MGM+": ["MGM+", "MGM Plus", "MGM+ East", "EPIX", "Epix", "EPIX 1"],
    "MGM+ Hits": ["MGM+ Hits", "MGM Hits", "EPIX Hits"],
    "Cinemax Action": ["Cinemax Action", "ActionMax", "Action Max"],
    "Cinemax Classics": ["Cinemax Classics", "5StarMax", "5 Star Max", "Five Star Max"],
    "Cinemax Hits HD": ["Cinemax Hits HD", "Cinemax Hits", "MoreMax", "More Max"],
    # Premium movies - lineups still using the pre-rebrand multiplex names
    "Action Max": ["Action Max", "ActionMax", "Cinemax Action"],
    "Five Star Max": ["Five Star Max", "5StarMax", "5 Star Max", "Cinemax Classics"],
    "More Max": ["More Max", "MoreMax", "Cinemax Hits"],
    "HBO Signature": ["HBO Signature", "HBO Drama", "HBO Drama HD"],
    "HBO Zone": ["HBO Zone", "HBO Movies", "HBO Movies HD"],
    "HBO 2": ["HBO 2", "HBO2", "HBO Hits"],
    # Entertainment / lifestyle
    "Magnolia Network": ["Magnolia Network", "Magnolia", "DIY Network", "DIY"],
    # "GAC" is NOT listed as an alias VALUE — it normalizes to "gac" (3 chars)
    # and would force a false score-100 match on any stream that abbreviates to
    # those letters. The full names carry the alias instead.
    "Great American Family": ["Great American Family", "Great American Country", "GAC Family"],
    "Great American Country": ["Great American Country", "Great American Family"],
    # Tastemade Home/Travel are sibling FAST channels; the lineup carries one
    # Tastemade entry and folds the Home variant in (shared EPG row upstream).
    "Tastemade": ["Tastemade", "Tastemade Home"],
    "Hallmark Drama": ["Hallmark Drama", "Hallmark Family"],
    # News - DISH lineup still carries the pre-rebrand "WGN America" name.
    "WGN America": ["WGN America", "WGN", "NewsNation", "News Nation"],

    # --- US: EPG guide-name bridges ---------------------------------------
    # Some US EPG sources list channels under their full legal/broadcast name
    # (e.g. "Daystar Television Network" instead of "Daystar"). At Exact match
    # sensitivity the short lineup name never reaches those entries, so the
    # channel gets no guide. These aliases bridge the lineup name to the EPG
    # entry name. NOTE: "TBN" maps to Trinity Broadcasting, NOT "TBN Inspire"
    # (the Hillsong rebrand, a separate channel - see "Hillsong Channel").
    "Daystar": ["Daystar", "Daystar Television Network", "Daystar Television Network HD"],
    "TBN": ["TBN", "Trinity Broadcasting Network", "Trinity Broadcasting Network HD (TBN)"],
    # Lineup channel "UP" maps to UPtv. Bare "UP" is intentionally NOT a value
    # (it would 100-match any stream normalizing to "up").
    "UP": ["UPtv", "UPTV", "UP TV"],
    "Cheddar News": ["Cheddar News", "Cheddar"],
    "Galavisión": ["Galavision", "Galavision Cable Network", "Galavision Cable Network HD"],
    "UniMás": ["UniMas", "UniMas East HD (UNIMAS)", "UniMas East", "Unimas"],
    "NBC Universo": ["NBC Universo", "Universo", "UNIVERSO"],
    # Reverse/abbreviation bridges: lineup uses the full name, streams use the
    # short form (or vice versa). normalize_name() strips a standalone "Channel"
    # token but NOT a glued one, so the glued "REELZCHANNEL" form is listed
    # explicitly (the spaced "Reelz Channel" would fold to "Reelz").
    "Turner Classic Movies": ["Turner Classic Movies", "TCM"],
    "REELZ": ["REELZ", "Reelz", "ReelzChannel", "REELZCHANNEL"],
    # More full-name / rebrand / abbreviation bridges confirmed against real US
    # streams (provider uses a fuller or rebranded name than the lineup).
    "YES Network": ["YES Network", "YES National"],
    "Sportsnet New York National": ["Sportsnet New York National", "SNY", "Sportsnet New York", "Sportsnet NY"],
    "CCTV News": ["CCTV News", "CGTN"],  # CCTV News rebranded to CGTN
    "Antena 3": ["Antena 3", "Antena 3 Internacional"],
    "Univision tINovelas": ["Univision tINovelas", "Univision tlnovelas", "Univision tlNovelas", "tlnovelas"],
    "TV Games Network": ["TV Games Network", "TVG Network"],
    "Christian Television Network": ["Christian Television Network", "CTN"],
}

# Country-scoped overrides (Phase 2), keyed by channel database code. The merge mechanism
# in Plugin._build_alias_map honors this dict per the selected channel_database.
COUNTRY_ALIASES = {
    "DO": {
        "A&E Mundo": ["A&E Mundo Latinoamérica", "A&E Mundo Latinoamerica"],
        "Acento TV": ["Acento TV HD"],
        "Amé 47": ["Amé", "Ame", "Ame 47"],
        "Az Corazón": [
            "Azteca Corazón",
            "Azteca Corazon",
            "Azteca Corazón HD",
            "Azteca Corazon HD",
        ],
        "Az Mundo": ["Azteca Mundo"],
        "Baby Tv": ["Baby TV", "BabyTV"],
        "C-Music HD": ["C-Music", "C Music", "C Music HD"],
        "Cadena del Milagro": ["Cadena Del Milagro"],
        "Canal de las Estrellas": ["Las Estrellas"],
        "Canal de las Estrellas HD": ["Las Estrellas HD"],
        "Caribbean TV HD": ["Caribbean TV"],
        "CDN Sports Max": [
            "CDN SportsMax",
            "CDN Deportes",
            "CDN Sports",
            "CDN Deportes Max",
        ],
        "CDN Sports Max HD": [
            "CDN SportsMax HD",
            "CDN Deportes HD",
            "CDN Sports HD",
            "CDN Deportes Max HD",
        ],
        "Cinecanal HD": ["CineCanal HD"],
        "CNN Español": [
            "CNN en Español",
            "CNN Espanol",
            "CNN en Espanol",
            "CNN Spanish",
        ],
        "CNN Headline News": ["Headline News", "HLN", "CNN HLN", "HLN Headline News"],
        "CNN Internacional": ["CNN International", "CNNi"],
        "CNN Live USA": ["CNN Live"],
        "Color Vision HD": ["Color Visión HD", "Colorvision HD"],
        "De Pelicula HD": ["De Película HD"],
        "DIscovery Channel HD": ["Discovery Channel HD"],
        "E! Entertainment": ["E!", "E Entertainment", "E Entertainment Television"],
        "Enlace Cristiano": ["Enlace TBN"],
        "Entelevisión": ["Entelevisión HD", "Entelevision", "Entelevision HD"],
        "Escogido HD": ["Escogido TV", "Escogido TV HD"],
        "ESPN 2": ["ESPN2"],
        "ESPN 3": ["ESPN3"],
        "ESPN 3 HD": ["ESPN3 HD"],
        "Europa Europa HD": ["Europa HD", "Europa Europa"],
        "FilmZone HD": ["Film Zone HD"],
        "Fox LA HD": [
            "Fox HD",
            "Canal Fox HD",
            "Fox Latinoamérica HD",
            "Fox Latinoamerica HD",
        ],
        "Fox Latin": ["Canal Fox", "Fox Latinoamérica", "Fox Latinoamerica"],
        "Fox Sport 3 HD": ["Fox Sports 3 HD"],
        "FXM": ["FX Movies", "FX Movie Channel", "Fox Movie Channel", "FXMovie"],
        "Gigantes HD": ["Gigantes TV", "Gigantes TV HD"],
        "Go HD": ["GO TV", "GO TV HD"],
        "Gourmet HD": ["El Gourmet HD", "The Gourmet HD"],
        "I.Sat": ["Isat", "I Sat"],
        "Licey HD": ["Licey TV", "Licey TV HD"],
        "Nat Geo LA HD": ["National Geographic HD", "NatGeo HD", "Nat Geo HD"],
        "NBA TV HD": ["NBA TV", "NBATV"],
        "Nickelodeon HD": ["Nick HD"],
        "RAI Italia": ["RAI"],
        "Ritmo Son": ["Ritmoson"],
        "Romana TV": ["Romana TV (La Romana)"],
        "Señales TV": [
            "Señales TV (Sto. Dgo.)",
            "Senales TV",
            "Senales TV (Sto. Dgo.)",
            "Señales TV Santo Domingo",
        ],
        "Sony Entertainment Televisión": ["Sony", "Sony Entertainment Television"],
        "Sony HD": [
            "Sony Entertainment Televisión HD",
            "Sony Entertainment Television HD",
        ],
        "Sy-Fy Channel": ["SyFy", "Sci-Fi", "SciFi"],
        "Telemundo Internacional": ["Telemundo"],
        "Teleradioamérica HD": ["Teleradio América HD", "Teleradio America HD"],
        "Televisa Deportes Network (TDN)": ["TDN", "Televisa Deportes Network"],
        "Televisa Deportes Network (TDN) HD": [
            "TDN HD",
            "Televisa Deportes Network HD",
        ],
        "The Golf Channel": ["Golf Channel", "Golf HD", "Golf Channel HD"],
        "The Gourmet": ["El Gourmet", "Gourmet"],
        "TL Novelas": ["TLNovelas", "TLNovelas HD"],
        "Toros HD": ["Toros TV", "Toros TV HD"],
        "TruTV": ["Tru TV", "truTV"],
        "TVE Internacional": ["RTVE"],
        "TVO Romana": [
            "TVO",
            "TVO (Televisión Oriental 49)",
            "TVO (Television Oriental 49)",
            "Televisión Oriental 49",
            "Television Oriental 49",
        ],
        "Universal Channel": ["Universal TV"],
        "Universal HD": ["Universal TV HD", "Universal Channel HD"],
        "VePlus TV": ["Venevisión Plus", "Venevision Plus", "Ve Plus TV"],
        "VePlus TV HD": ["Venevisión Plus HD", "Venevision Plus HD", "Ve Plus TV HD"],
        "Warner Channel HD": ["Warner HD"],
        "Xtremo Premium": ["Extremo Premium"],
        "Telemicro": ["Canal 5", "Telemicro 5"],
        "Telemicro HD": ["Telemicro 5 HD", "Canal 5 HD"],
        "CERTV": ["Channel 4", "Canal 4"],
        "Claro Sports": ["Claro Sport"],
        "Claro Sports HD": ["Claro Sport HD"],
        "Color Visión": ["Colorvision", "Color Vision"],
        "GDM Estudio": [
            "GDM Television",
            "GDM Televisión",
            "GDM Television HD",
            "GDM Televisión HD",
        ],
        "Mía TV": ["Miavision", "Mia Vision", "Mía Visión"],
        "Canal 10": ["Onda TV Canal 10", "Onda Canal 10"],
        "RNN": ["RNN Canal 27"],
        "Supercanal": [
            "Super Canal 33",
            "Super Canal 33 HD",
            "Supercanal 33",
            "Supercanal 33 HD",
        ],
        "SuperTV55": ["Super TV 55", "Super TV 55 HD"],
        "Telecanal": ["Telecanal 12"],
        "Telecentro": ["Telecentro 13"],
        "Teleradio América": ["Teleradio", "Teleradio America"],
        "Telesistema": ["Telesistema 11"],
        "La Voz de María": ["Voz de Maria", "Voz de María"],
        "Maimón TV": ["MAIMON TV", "MAIMON TV 3", "MAIMON TV CANAL 3", "Maimon TV"],
        "GH Television": ["GH TELEVISION", "GH TELEVISION CANAL 10", "GH TV"],
        "Microvisión": [
            "MICROVISION",
            "MICROVISION CANAL 10",
            "Microvision",
            "Micro Vision",
        ],
        "ORBIT TV": ["ORBIT TV", "ORBIT TV CANAL 10", "Orbit TV"],
        "Yuna Visión": ["YUNA TV", "YUNA VISION", "YUNA VISION CANAL 10", "Yunavision"],
        "Fuego TV": ["FUEGO TV", "FUEGO TV CANAL 40", "Fuego 40"],
        "La Ruta 66 TV": ["LA RUTA TV", "LA RUTA 66 TV", "Ruta 66 TV"],
        "El 75": ["EL 75", "CANAL 75", "EL75"],
        "Zol FM TV": [
            "ZOL 106 HD",
            "ZOL 106",
            "ZOL 106.5",
            "ZOL 106.5 FM",
            "ZOL FM TV",
        ],
        "Digital809 TV": ["DIGITAL809 TV", "DIGITAL 809 TV", "Digital809"],
        "Televisión Dominicana": [
            "DOMINICANA HD",
            "TELEVISION DOMINICANA",
            "TELEVISIÓN DOMINICANA",
            "TV DOMINICANA",
        ],
    },
    "PE": {
        "Adrenalina Sports Network": ["Adrenalina Sports", "ASN Sports"],
        "Adrenalina Sports Network HD": ["Adrenalina Sports HD", "ASN Sports HD"],
        "ALACOCINA TV": ["A la Cocina TV", "Alacocina TV"],
        "ALACOCINA TV HD": ["A la Cocina TV HD", "Alacocina TV HD"],
        "América Trujillo": [
            "America Trujillo",
            "América TV Trujillo",
            "America TV Trujillo",
        ],
        "América TV": ["America TV", "América Televisión", "America Television"],
        "América TV HD": [
            "America TV HD",
            "América Televisión HD",
            "America Television HD",
        ],
        "Antena 3 Internacional": ["Antena 3"],
        "ARIRANG": ["Arirang"],
        "ATV": ["Andina de Televisión", "Andina de Television", "ATV Perú", "ATV Peru"],
        "ATV HD": [
            "Andina de Televisión HD",
            "Andina de Television HD",
            "ATV Perú HD",
            "ATV Peru HD",
        ],
        "ATV Sur": ["ATV+ Sur", "ATV Plus Sur"],
        "Azteca Mundo": ["Az Mundo"],
        "Canal del Congreso": ["Congreso TV", "Canal Congreso"],
        "Caracol Internacional": ["Caracol", "Nuestra Tele"],
        "Cartoonito": ["Boomerang"],
        "Cinecanal": ["CineCanal"],
        "Cinelatino": ["Cine Latino", "CineLatino"],
        "Claro Sports HD": ["Claro Sports Perú HD", "Claro Sports Peru HD"],
        "CNN en Español": ["CNN Español", "CNN Espanol", "CNN Spanish"],
        "CNN International": ["CNN Internacional", "CNNi"],
        "El Gourmet": ["The Gourmet", "Gourmet"],
        "ESNE TV": ["ESNE", "El Sembrador", "El Sembrador Nueva Evangelización"],
        "Exitosa": ["Exitosa TV"],
        "Exitosa HD": ["Exitosa TV HD"],
        "France 24 Español HD": [
            "France 24 Español",
            "France24 Español",
            "France 24 Spanish",
        ],
        "Global": ["Global TV", "América Next", "America Next"],
        "Global HD": ["Global TV HD", "América Next HD", "America Next HD"],
        "HBO Xtreme": ["HBO Extreme"],
        "HBO Xtreme HD": ["HBO Extreme HD"],
        "IPE": ["Canal IPE", "IPe"],
        "IPE HD": ["Canal IPE HD", "IPe HD"],
        "L1 HD": ["Liga 1", "Liga1", "L1", "Liga 1 HD", "Liga1 HD"],
        "L1 MAX HD": [
            "Liga 1 Max",
            "Liga1 Max",
            "L1 Max",
            "Liga 1 Max HD",
            "Liga1 Max HD",
        ],
        "L1 MAX HD (Superior Pro)": [
            "L1 Max Superior Pro",
            "Liga 1 Max Superior Pro",
            "Liga1 Max Superior Pro",
        ],
        "La Tele": ["LaTele"],
        "Las Estrellas": ["Canal de las Estrellas", "Canal Las Estrellas"],
        "Latina": ["Latina Televisión", "Latina Television"],
        "Latina HD": ["Latina Televisión HD", "Latina Television HD"],
        "Nativa": ["Nativa TV"],
        "Nativa HD": ["Nativa TV HD"],
        "Nick Music": ["NickMusic"],
        "Panamericana": [
            "Panamericana TV",
            "Panamericana Televisión",
            "Panamericana Television",
        ],
        "Panamericana HD": [
            "Panamericana TV HD",
            "Panamericana Televisión HD",
            "Panamericana Television HD",
        ],
        "PBO": ["PBO Radio", "PBO TV"],
        "PBO HD": ["PBO TV HD"],
        "RPP": ["RPP TV", "RPP Noticias"],
        "Sol TV+": ["Sol TV", "Sol TV Plus"],
        "Telehit Música Plus": ["Telehit Musica Plus", "Telehit Plus"],
        "TLNovelas": ["TL Novelas", "tlnovelas"],
        "TV Cosmos": ["Cosmos TV"],
        "TV Perú": ["TV Peru", "TVPerú"],
        "TV Perú 7.3": ["TV Peru 7.3", "TVPerú 7.3"],
        "TV Perú 7.3 HD": ["TV Peru 7.3 HD", "TVPerú 7.3 HD"],
        "TV Perú HD": ["TV Peru HD", "TVPerú HD"],
        "TV5Monde": ["TV5 Monde"],
        "TyC Sports": ["TyC", "TyC Sports Internacional"],
        "Universal Premiere": ["Universal Premier"],
        "Universal Premiere 2": ["Universal Premier 2"],
        "Viva TV": ["Viva TV Perú", "Viva TV Peru"],
        "Willax": ["Willax TV"],
        "Willax HD": ["Willax TV HD"],
    },
}
