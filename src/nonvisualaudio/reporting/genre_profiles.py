"""Static genre reference targets used for comparison mode.

Values are based on widely used loudness standards and common mastering
practice (EBU R128 for broadcast and podcasting, common streaming loudness
targets for music, and observed mastering trends in each sub-genre). They
are intentionally conservative typical values, not hard rules.

Genres are grouped into categories so the UI can render them with visual
separators. Each sub-genre has its own reference values so that comparison
reports can be as specific as the user wants.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GenreProfile:
    key: str
    display_name: str
    category: str
    target_lufs: float
    lra_low: float
    lra_high: float
    notes: str  # short description of typical character


# Category order used by the UI to group the combo box.
CATEGORY_ORDER: tuple[str, ...] = (
    "Audio Drama",
    "Podcast",
    "Spoken Word",
    "Pop",
    "Rock",
    "Rap and Hip Hop",
    "R and B and Soul",
    "Reggae",
    "Jazz",
    "Folk and Country",
    "Electronic",
    "Classical",
    "Film and Cinema",
)


_PROFILES: tuple[GenreProfile, ...] = (
    # --- Audio Drama -------------------------------------------------------
    GenreProfile(
        key="audio_drama_classic",
        display_name="Audio Drama — Classic Broadcast",
        category="Audio Drama",
        target_lufs=-23.0,
        lra_low=10.0,
        lra_high=20.0,
        notes="wide dynamic range with rich midrange for dialogue, mastered to EBU R128 broadcast standards",
    ),
    GenreProfile(
        key="audio_drama_modern",
        display_name="Audio Drama — Modern Commercial",
        category="Audio Drama",
        target_lufs=-16.0,
        lra_low=6.0,
        lra_high=10.0,
        notes="loud, controlled master typical of modern commercial audio dramas released on streaming platforms",
    ),
    GenreProfile(
        key="audio_drama_feature",
        display_name="Audio Drama — Radio Feature",
        category="Audio Drama",
        target_lufs=-20.0,
        lra_low=8.0,
        lra_high=14.0,
        notes="documentary style production with moderate dynamics and clear dialogue",
    ),
    # --- Podcast -----------------------------------------------------------
    GenreProfile(
        key="podcast_conversation",
        display_name="Podcast — Conversational",
        category="Podcast",
        target_lufs=-16.0,
        lra_low=5.0,
        lra_high=9.0,
        notes="voice forward, controlled dynamics, limited low end",
    ),
    GenreProfile(
        key="podcast_narrative",
        display_name="Podcast — Narrative (Scripted)",
        category="Podcast",
        target_lufs=-18.0,
        lra_low=6.0,
        lra_high=10.0,
        notes="cinematic storytelling style with music beds, wider dynamics than typical podcasts",
    ),
    GenreProfile(
        key="podcast_news",
        display_name="Podcast — News or Interview",
        category="Podcast",
        target_lufs=-16.0,
        lra_low=4.0,
        lra_high=8.0,
        notes="tight, consistent voice level for commute and mobile listening",
    ),
    # --- Spoken Word -------------------------------------------------------
    GenreProfile(
        key="spoken_audiobook",
        display_name="Spoken Word — Audiobook",
        category="Spoken Word",
        target_lufs=-19.0,
        lra_low=6.0,
        lra_high=10.0,
        notes="narration oriented, midrange focused, minimal low end, Audible RMS target around minus 18 to minus 20 dB",
    ),
    GenreProfile(
        key="spoken_poetry",
        display_name="Spoken Word — Poetry or Performance",
        category="Spoken Word",
        target_lufs=-18.0,
        lra_low=8.0,
        lra_high=14.0,
        notes="expressive delivery with room for dramatic dynamic shifts",
    ),
    GenreProfile(
        key="spoken_lecture",
        display_name="Spoken Word — Lecture or Talk",
        category="Spoken Word",
        target_lufs=-20.0,
        lra_low=5.0,
        lra_high=9.0,
        notes="clear instructional voice, calm dynamics, broadcast compliant",
    ),
    # --- Pop ---------------------------------------------------------------
    GenreProfile(
        key="pop_modern",
        display_name="Pop — Modern Commercial",
        category="Pop",
        target_lufs=-9.0,
        lra_low=3.0,
        lra_high=6.0,
        notes="very loud master, heavily limited, bright and upfront vocals, tight low end",
    ),
    GenreProfile(
        key="pop_streaming",
        display_name="Pop — Streaming Target",
        category="Pop",
        target_lufs=-14.0,
        lra_low=5.0,
        lra_high=8.0,
        notes="conservative master aimed at Spotify or Apple Music loudness normalization, with more breathing room",
    ),
    GenreProfile(
        key="pop_8090",
        display_name="Pop — 80s or 90s",
        category="Pop",
        target_lufs=-14.0,
        lra_low=7.0,
        lra_high=12.0,
        notes="open dynamics, reverberant drums, bright presence, less aggressive limiting than today",
    ),
    GenreProfile(
        key="pop_indie",
        display_name="Pop — Indie or Bedroom",
        category="Pop",
        target_lufs=-12.0,
        lra_low=5.0,
        lra_high=9.0,
        notes="warm, slightly lo-fi character with natural vocals and moderate compression",
    ),
    # --- Rock --------------------------------------------------------------
    GenreProfile(
        key="rock_classic",
        display_name="Rock — Classic or Vintage",
        category="Rock",
        target_lufs=-14.0,
        lra_low=8.0,
        lra_high=14.0,
        notes="analog-style mix with open dynamics, warm midrange guitars, and natural drum transients",
    ),
    GenreProfile(
        key="rock_modern",
        display_name="Rock — Modern Alternative",
        category="Rock",
        target_lufs=-9.0,
        lra_low=4.0,
        lra_high=7.0,
        notes="loud, dense master with heavy guitars, bright cymbals, compressed drums",
    ),
    GenreProfile(
        key="rock_metal",
        display_name="Rock — Metal or Hard Rock",
        category="Rock",
        target_lufs=-8.0,
        lra_low=3.0,
        lra_high=6.0,
        notes="very loud, heavily scooped midrange, aggressive high end, subsonic kick drum",
    ),
    # --- Rap and Hip Hop ---------------------------------------------------
    GenreProfile(
        key="rap_old_school",
        display_name="Rap and Hip Hop — Old School",
        category="Rap and Hip Hop",
        target_lufs=-10.0,
        lra_low=6.0,
        lra_high=9.0,
        notes="punchy drums and present vocals, moderately dynamic compared to modern rap",
    ),
    GenreProfile(
        key="rap_modern_trap",
        display_name="Rap and Hip Hop — Modern Trap",
        category="Rap and Hip Hop",
        target_lufs=-7.0,
        lra_low=3.0,
        lra_high=6.0,
        notes="very loud, saturated sub bass, heavily limited vocals",
    ),
    GenreProfile(
        key="rap_boom_bap",
        display_name="Rap and Hip Hop — Boom Bap or Lo-Fi",
        category="Rap and Hip Hop",
        target_lufs=-12.0,
        lra_low=7.0,
        lra_high=10.0,
        notes="sampled drum feel, warmer character, more dynamic headroom than modern trap",
    ),
    # --- R and B and Soul --------------------------------------------------
    GenreProfile(
        key="rnb_modern",
        display_name="R and B — Modern",
        category="R and B and Soul",
        target_lufs=-10.0,
        lra_low=4.0,
        lra_high=8.0,
        notes="smooth, sub-forward low end, airy vocals with subtle saturation and polished top",
    ),
    GenreProfile(
        key="rnb_classic_soul",
        display_name="R and B — Classic Soul or Motown",
        category="R and B and Soul",
        target_lufs=-14.0,
        lra_low=7.0,
        lra_high=12.0,
        notes="warm midrange, natural horns and strings, open dynamics, vintage tape character",
    ),
    GenreProfile(
        key="rnb_neo_soul",
        display_name="R and B — Neo Soul",
        category="R and B and Soul",
        target_lufs=-12.0,
        lra_low=6.0,
        lra_high=10.0,
        notes="jazz-informed chord textures, lush keys, smooth vocals, relaxed punchy drums",
    ),
    # --- Reggae ------------------------------------------------------------
    GenreProfile(
        key="reggae_roots",
        display_name="Reggae — Roots or Dub",
        category="Reggae",
        target_lufs=-12.0,
        lra_low=6.0,
        lra_high=10.0,
        notes="heavy bass guitar foundation, warm midrange, spacious reverbs, relaxed tempo feel",
    ),
    GenreProfile(
        key="reggae_dancehall",
        display_name="Reggae — Dancehall",
        category="Reggae",
        target_lufs=-8.0,
        lra_low=4.0,
        lra_high=7.0,
        notes="loud, club-oriented master with strong sub bass and sharp, forward vocals",
    ),
    GenreProfile(
        key="reggae_lovers_rock",
        display_name="Reggae — Lovers Rock or Ska",
        category="Reggae",
        target_lufs=-13.0,
        lra_low=6.0,
        lra_high=10.0,
        notes="smoother, more pop-leaning balance with warm bass, bright horns, and mellow vocals",
    ),
    # --- Jazz --------------------------------------------------------------
    GenreProfile(
        key="jazz_acoustic",
        display_name="Jazz — Acoustic or Ensemble",
        category="Jazz",
        target_lufs=-20.0,
        lra_low=10.0,
        lra_high=18.0,
        notes="natural room recording with wide dynamics, airy cymbals, upright bass, minimal compression",
    ),
    GenreProfile(
        key="jazz_fusion",
        display_name="Jazz — Fusion or Electric",
        category="Jazz",
        target_lufs=-16.0,
        lra_low=8.0,
        lra_high=14.0,
        notes="mixed acoustic and electric elements with more punch than acoustic jazz but still open",
    ),
    GenreProfile(
        key="jazz_vocal",
        display_name="Jazz — Vocal Standards",
        category="Jazz",
        target_lufs=-18.0,
        lra_low=8.0,
        lra_high=14.0,
        notes="intimate vocal focus, subtle orchestral backing, clear diction, polite dynamics",
    ),
    # --- Folk and Country --------------------------------------------------
    GenreProfile(
        key="folk_acoustic",
        display_name="Folk — Acoustic Singer-Songwriter",
        category="Folk and Country",
        target_lufs=-16.0,
        lra_low=7.0,
        lra_high=12.0,
        notes="voice and acoustic guitar foregrounded, natural dynamics, clear midrange",
    ),
    GenreProfile(
        key="country_modern",
        display_name="Country — Modern Nashville",
        category="Folk and Country",
        target_lufs=-10.0,
        lra_low=4.0,
        lra_high=7.0,
        notes="polished, pop-adjacent master with bright vocals, punchy drums, tight low end",
    ),
    GenreProfile(
        key="country_traditional",
        display_name="Country — Traditional or Americana",
        category="Folk and Country",
        target_lufs=-14.0,
        lra_low=7.0,
        lra_high=12.0,
        notes="natural acoustic instruments, warm midrange, wider dynamics than modern country",
    ),
    # --- Electronic --------------------------------------------------------
    GenreProfile(
        key="electronic_club",
        display_name="Electronic — Club or EDM",
        category="Electronic",
        target_lufs=-7.0,
        lra_low=3.0,
        lra_high=6.0,
        notes="club ready loudness with strong sub bass and limited dynamics",
    ),
    GenreProfile(
        key="electronic_techno_house",
        display_name="Electronic — Techno or House",
        category="Electronic",
        target_lufs=-9.0,
        lra_low=4.0,
        lra_high=7.0,
        notes="steady four on the floor, full low end, moderate dynamics for DJ mixing",
    ),
    GenreProfile(
        key="electronic_ambient",
        display_name="Electronic — Ambient or Downtempo",
        category="Electronic",
        target_lufs=-14.0,
        lra_low=8.0,
        lra_high=14.0,
        notes="spacious, textural, considerably more dynamic than dance music",
    ),
    GenreProfile(
        key="electronic_idm",
        display_name="Electronic — IDM or Experimental",
        category="Electronic",
        target_lufs=-12.0,
        lra_low=6.0,
        lra_high=12.0,
        notes="detailed transients and wide dynamic contrasts",
    ),
    # --- Classical ---------------------------------------------------------
    GenreProfile(
        key="classical_chamber",
        display_name="Classical — Solo or Chamber",
        category="Classical",
        target_lufs=-20.0,
        lra_low=14.0,
        lra_high=22.0,
        notes="intimate recording with natural dynamics and close instrument detail",
    ),
    GenreProfile(
        key="classical_orchestral",
        display_name="Classical — Symphonic or Orchestral",
        category="Classical",
        target_lufs=-18.0,
        lra_low=16.0,
        lra_high=24.0,
        notes="very wide dynamic range from whisper quiet to full tutti",
    ),
    GenreProfile(
        key="classical_opera",
        display_name="Classical — Opera or Vocal",
        category="Classical",
        target_lufs=-19.0,
        lra_low=14.0,
        lra_high=22.0,
        notes="vocal focused with orchestral accompaniment, substantial dynamic range",
    ),
    GenreProfile(
        key="classical_early",
        display_name="Classical — Baroque or Early Music",
        category="Classical",
        target_lufs=-20.0,
        lra_low=12.0,
        lra_high=18.0,
        notes="period instruments, smaller ensembles, natural room sound",
    ),
    # --- Film and Cinema ---------------------------------------------------
    GenreProfile(
        key="film_theatrical",
        display_name="Film — Theatrical Dialogue",
        category="Film and Cinema",
        target_lufs=-27.0,
        lra_low=15.0,
        lra_high=25.0,
        notes="cinema dialogue sitting around minus 27 LKFS with large effects headroom",
    ),
    GenreProfile(
        key="film_tv_drama",
        display_name="Film — TV Drama or Streaming",
        category="Film and Cinema",
        target_lufs=-24.0,
        lra_low=10.0,
        lra_high=18.0,
        notes="mixed for home viewing under ATSC A/85 or EBU R128 broadcast rules",
    ),
    GenreProfile(
        key="film_trailer",
        display_name="Film — Trailer or Promo",
        category="Film and Cinema",
        target_lufs=-14.0,
        lra_low=4.0,
        lra_high=8.0,
        notes="loud, attention grabbing master with compressed dynamics",
    ),
)


# Public lookup dict, keyed by profile key.
GENRES: dict[str, GenreProfile] = {p.key: p for p in _PROFILES}


def list_genres() -> list[GenreProfile]:
    """Return profiles in display order, grouped by category."""
    ordered: list[GenreProfile] = []
    seen = set()
    for category in CATEGORY_ORDER:
        for p in _PROFILES:
            if p.category == category and p.key not in seen:
                ordered.append(p)
                seen.add(p.key)
    return ordered


def grouped_genres() -> list[tuple[str, list[GenreProfile]]]:
    """Return genres grouped by category, preserving CATEGORY_ORDER."""
    groups: dict[str, list[GenreProfile]] = {c: [] for c in CATEGORY_ORDER}
    for p in _PROFILES:
        groups.setdefault(p.category, []).append(p)
    return [(c, groups[c]) for c in CATEGORY_ORDER if groups[c]]
