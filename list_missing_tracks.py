import os
import re
import argparse
from typing import List, Tuple
import unidecode
from spotipy.oauth2 import SpotifyClientCredentials
import spotipy
import itertools
from rapidfuzz import fuzz
from dotenv import load_dotenv

# === CONFIG ===
AUDIO_EXTENSIONS = ['.mp3', '.wav', '.flac', '.m4a', '.aiff']

# === ARTIST & TITLE HELPERS ===
def generate_artist_permutations(artists: List[str]) -> List[str]:
    """Return all unique artist order permutations (comma-separated strings)."""
    if not artists or len(artists) == 1:
        return [", ".join(artists)]
    perms = set(", ".join(p) for p in itertools.permutations(artists))
    return list(perms)

def sanitize_local_artist(artist_name: str) -> str:
    """Remove (ofc), (be), (uk), etc. from artist names."""
    return re.sub(r'\((ofc|be|uk|us|fr|it|ca|au|de)\)', '', artist_name, flags=re.IGNORECASE).strip()

def normalize_text(text: str) -> str:
    """Remove diacritics, unify feat., and strip symbols for fuzzy comparison."""
    text = unidecode.unidecode(text)
    text = re.sub(r'(?i)\b(feat|ft|featuring)\b', '', text)
    text = text.replace('&', 'and')
    text = re.sub(r'\((ofc|be|uk|us|fr|it|ca|au|de)\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).lower().strip()

def simplify_title(title: str, artist_list: List[str] = None) -> str:
    """Strip basic version descriptors and redundant features from a title."""
    title = re.sub(r'\((Original Mix|Extended Mix|Club Mix|Dub Mix|Version|Edit)\)', '', title, flags=re.IGNORECASE).strip()
    if artist_list:
        for artist in artist_list:
            pattern = rf'(?i)\s*(feat\.?|featuring)\s+{re.escape(artist)}'
            title = re.sub(pattern, '', title)
    title = re.sub(r'\s{2,}', ' ', title)
    return normalize_text(title)

def extract_artist_and_title(track_str: str) -> Tuple[List[str], str]:
    """
    Parse "Artist1, Artist2 - Title (feat. X)" into (artists: [..], title: ...), merging any (feat. ..) from title into artists.
    """
    if " - " in track_str:
        artist_part, title_part = track_str.split(" - ", 1)
        artist_raw_parts = re.split(r',|\s&\s|\sand\s|\sft\.?\s|\sfeat\.?\s|\sfeaturing\s', artist_part, flags=re.IGNORECASE)
        artists = [a.strip() for a in artist_raw_parts if a.strip()]

        m = re.search(r'\(feat\.? ([^)]+)\)', title_part, re.IGNORECASE)
        if m:
            feat_artists = [a.strip() for a in re.split(r',|&|and', m.group(1))]
            artists += [a for a in feat_artists if a not in artists]
            title_part = re.sub(r'\(feat\.? [^)]+\)', '', title_part, flags=re.IGNORECASE).strip()

        return artists, title_part.strip()
    return [], track_str.strip()

def extract_mix_type(title: str) -> str:
    """
    Return mix/remix/version info (e.g. 'Original Mix', 'Remix', 'Club Mix', etc.) from a title, lowercase, else ''.
    """
    m = re.search(r'\(([^)]+)\)$', title)
    if m:
        mix = m.group(1).strip().lower()
        if re.search(r'remix|mix|edit|version', mix):
            return mix
    m2 = re.search(r'-\s*([^-]+(?:remix|mix|edit|version))$', title, re.I)
    if m2:
        return m2.group(1).strip().lower()
    return ''

def strip_nonmix_subtitles(title: str) -> str:
    """
    Remove parentheticals and trailing hyphen subtitles NOT being remix/edit/mix/version.
    """
    t = re.sub(r'\((?!.*remix|edit|mix|version).*?\)', '', title, flags=re.I)
    t = re.sub(r'\s*-\s*((?!remix|edit|mix|version)[^-\n]+)$', '', t, flags=re.I)
    return t.strip()

def clean_filename(name: str) -> str:
    """Replace underscores/dots with spaces."""
    return re.sub(r'[_\.]', ' ', name).strip()

# === MATCHING LOGIC ===
def is_mix_type_conflict(mt1, mt2):
    """
    Returns True if:
    - one is a remix (any string with 'remix', or a non-neutral 'mix', 'edit', 'version'), and
      the other is neutral or blank.
    Allows neutral mixes (original/extended/club/etc) to match each other or blank.
    Only blocks remix/edit vs neutral/blank.
    """
    NEUTRAL = [
        'original mix', 'extended mix', 'club mix', 'club mix edit', 'radio edit', 'radio mix',
        'edit', 'version', 'mix', 'extended', 'original', 'extended club mix'
    ]

    mt1 = (mt1 or '').lower()
    mt2 = (mt2 or '').lower()

    def is_remix_type(mt):
        if not mt:
            return False
        if 'remix' in mt:
            return True
        if (('edit' in mt or 'mix' in mt or 'version' in mt) and mt not in NEUTRAL):
            return True
        return False

    if not mt1 and not mt2:
        return False
    if mt1 in NEUTRAL and mt2 in NEUTRAL:
        return False
    if (mt1 in NEUTRAL and not mt2) or (mt2 in NEUTRAL and not mt1):
        return False
    if (is_remix_type(mt1) and (mt2 in NEUTRAL or not mt2)) or (is_remix_type(mt2) and (mt1 in NEUTRAL or not mt1)):
        return True
    if is_remix_type(mt1) and is_remix_type(mt2):
        return False
    return mt1 != mt2

def get_local_track_names(folder_path: str) -> List[Tuple[str, str]]:
    tracks = []
    for root, _, files in os.walk(folder_path):
        for file in files:
            if any(file.lower().endswith(ext) for ext in AUDIO_EXTENSIONS):
                name, _ = os.path.splitext(file)
                formatted = clean_filename(name)
                tracks.append((formatted, normalize_text(formatted)))
    return tracks

def format_spotify_track(artists: List[str], title: str) -> str:
    """
    Format a Spotify API track into "Artist1, Artist2 - Title (Remix)" for consistent local matching.
    """
    # Remove (Original Mix) and (Extended Mix)
    title_clean = re.sub(r'\((Original Mix|Extended Mix)\)', '', title, flags=re.IGNORECASE)
    title_clean = re.sub(r'\b(Original Mix|Extended Mix)\b', '', title_clean, flags=re.IGNORECASE)
    title_clean = re.sub(r'\s*-\s*$', '', title_clean)
    title_clean = re.sub(r'\s{2,}', ' ', title_clean).strip()
    if ' - ' in title_clean and '(' not in title_clean:
        title_parts = title_clean.split(' - ', 1)
        title_clean = f"{title_parts[0].strip()} ({title_parts[1].strip()})"
    remix_match = re.search(r'\((.+? Remix.*?)\)', title_clean, flags=re.IGNORECASE)
    if remix_match:
        remixers_raw = remix_match.group(1).replace(' Remix', '').strip()
        remixers_split = re.split(r' and |, | & ', remixers_raw)
        remixers_list = [normalize_text(r) for r in remixers_split]
    else:
        remixers_list = []
    final_artists = []
    for a in artists:
        norm_a = normalize_text(a)
        if norm_a not in remixers_list:
            final_artists.append(a)
    artist_str = ', '.join(final_artists)
    return f"{artist_str} - {title_clean}"

def fetch_spotify_playlist_tracks(playlist_url: str, client_id: str, client_secret: str) -> List[str]:
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=client_id, client_secret=client_secret))
    playlist_id = playlist_url.split("/")[-1].split("?")[0]
    results = sp.playlist_items(playlist_id)
    tracks = []
    while results:
        for item in results['items']:
            track = item['track']
            if track:
                artists = [a['name'] for a in track['artists']]
                title = track['name']
                formatted = format_spotify_track(artists, title)
                tracks.append(formatted)
        results = sp.next(results) if results['next'] else None
    return sorted(tracks, key=str.lower)

def find_matches(spotify_tracks: List[str], local_tracks: List[Tuple[str, str]], threshold: int = 90):
    matched = []
    missing = []
    matched_locals = set()
    for sp_track in spotify_tracks:
        sp_artists, sp_title = extract_artist_and_title(sp_track)
        sp_title_for_match = strip_nonmix_subtitles(sp_title)
        sp_title_normalized = simplify_title(sp_title_for_match, sp_artists)
        sp_mix_type = extract_mix_type(sp_title)
        sp_artist_perms = generate_artist_permutations([sanitize_local_artist(a) for a in sp_artists])
        sp_artist_norms = [normalize_text(perm) for perm in sp_artist_perms]
        found = False
        for local_original, local_normalized in local_tracks:
            if local_original in matched_locals:
                continue
            loc_artists, loc_title = extract_artist_and_title(local_original)
            loc_title_for_match = strip_nonmix_subtitles(loc_title)
            loc_title_normalized = simplify_title(loc_title_for_match, loc_artists)
            loc_mix_type = extract_mix_type(loc_title)
            loc_artist_perms = generate_artist_permutations([sanitize_local_artist(a) for a in loc_artists])
            loc_artist_norms = [normalize_text(perm) for perm in loc_artist_perms]
            if is_mix_type_conflict(sp_mix_type, loc_mix_type):
                continue
            best_artist_score = 0
            for sp_norm in sp_artist_norms:
                for loc_norm in loc_artist_norms:
                    score = fuzz.partial_ratio(sp_norm, loc_norm)
                    if score > best_artist_score:
                        best_artist_score = score
            title_score = fuzz.partial_ratio(sp_title_normalized, loc_title_normalized)
            if best_artist_score >= threshold and title_score >= threshold:
                matched.append(sp_track)
                matched_locals.add(local_original)
                found = True
                break
        if not found:
            missing.append(sp_track)
    unmatched_local = [original for original, _ in local_tracks if original not in matched_locals]
    return matched, missing, unmatched_local

def save_list_to_file(data: List[str], path: str, label: str):
    with open(path, "w", encoding="utf-8") as f:
        for item in sorted(data, key=str.lower):
            f.write(item.strip() + "\n")
    print(f"📄 {label} saved to {path}")

def format_local_track_name(raw_name: str) -> str:
    """Return normalized display of local file name (wraps mix type in parentheses if missing)."""
    raw_name = clean_filename(raw_name)
    if " - " in raw_name:
        artists, title = raw_name.split(" - ", 1)
        artists = artists.strip()
        title = title.strip()
        common_suffixes = [
            'Remix', 'Extended Mix', 'Original Mix', 'Club Mix',
            'Radio Edit', 'Edit', 'Dub Mix', 'Version'
        ]
        if not re.search(r'\(.*\)', title):
            for suffix in common_suffixes:
                if title.lower().endswith(suffix.lower()):
                    title = re.sub(rf'{suffix}$', f'({suffix})', title, flags=re.IGNORECASE)
                    break
        return f"{artists} - {title}"
    return raw_name.strip()

def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description='Sync Spotify playlist with local DJ folder.')
    parser.add_argument('spotify_url', help='Spotify playlist URL')
    parser.add_argument('--folder_path', help='Path to local folder', default=None)
    parser.add_argument('--client_id', help='Spotify Client ID (optional if set in .env)')
    parser.add_argument('--client_secret', help='Spotify Client Secret (optional if set in .env)')
    args = parser.parse_args()
    client_id = args.client_id or os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = args.client_secret or os.getenv("SPOTIPY_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("❌ Spotify credentials missing. Provide via CLI or set in .env")
        exit(1)
    print("🔗 Fetching playlist...")
    spotify_tracks = fetch_spotify_playlist_tracks(args.spotify_url, client_id, client_secret)
    print(f"🎵 Found {len(spotify_tracks)} tracks.")
    if args.folder_path:
        print("📂 Reading local files...")
        local_tracks = get_local_track_names(args.folder_path)
        print(f"📁 {len(local_tracks)} local files found.")
        print("🧠 Matching tracks...")
        matched, missing, unmatched_local = find_matches(spotify_tracks, local_tracks)
        formatted_unmatched = [format_local_track_name(name) for name in unmatched_local]
        os.makedirs("logs", exist_ok=True)
        save_list_to_file(missing, "logs/missing_tracks.txt", "Missing tracks")
        save_list_to_file(matched, "logs/matched_tracks.txt", "Matched tracks")
        save_list_to_file(formatted_unmatched, "logs/unmatched_local.txt", "Unmatched local files")
        print(f"\n✅ {len(matched)} matched")
        print(f"❌ {len(missing)} missing")
        print(f"📂 {len(unmatched_local)} unmatched locals")
        if len(matched) != len(local_tracks):
            print("\n⚠️ Mismatch detected between matched and local files.")
            print("Check 'unmatched_local.txt' for strays.")
    else:
        print("\n🎧 All Spotify tracks:")
        for track in spotify_tracks:
            print(f" - {track}")

if __name__ == '__main__':
    main()
