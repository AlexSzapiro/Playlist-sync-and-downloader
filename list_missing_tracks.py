import os
import re
import argparse
from typing import List, Tuple
import unidecode
from spotipy.oauth2 import SpotifyClientCredentials
import spotipy
from rapidfuzz import fuzz
from dotenv import load_dotenv

AUDIO_EXTENSIONS = ['.mp3', '.wav', '.flac', '.m4a', '.aiff']



def normalize_text(text: str) -> str:
    text = unidecode.unidecode(text)
    text = re.sub(r'(?i)\b(feat|ft|featuring)\b', '', text)
    text = text.replace('&', 'and')
    text = re.sub(r'\((ofc|be|uk|us|fr|it|ca|au|de)\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).lower().strip()

def simplify_title(title: str, artist_list: List[str] = None) -> str:
    # Remove version descriptors
    title = re.sub(r'\((Original Mix|Extended Mix|Club Mix|Dub Mix|Version|Edit)\)', '', title, flags=re.IGNORECASE).strip()

    # Remove "feat. X" in title if X is already in artist list
    if artist_list:
        for artist in artist_list:
            escaped = re.escape(artist)
            pattern = rf'(?i)\s*(feat\.?|featuring)\s+{escaped}'
            title = re.sub(pattern, '', title)

    title = re.sub(r'\s{2,}', ' ', title)
    return normalize_text(title)

def clean_filename(name: str) -> str:
    # Replace underscores, dots etc with spaces
    name = re.sub(r'[_\.]', ' ', name)
    return name.strip()

def extract_artist_and_title(track_str: str) -> Tuple[List[str], str]:
    if " - " in track_str:
        artist_part, title_part = track_str.split(" - ", 1)

        # Split artists by commas, "&", "and", "feat", "ft", "featuring"
        artist_raw_parts = re.split(r',|\s&\s|\sand\s|\sft\s|\sfeat\.?\s|\sfeaturing\s', artist_part, flags=re.IGNORECASE)
        artists = [a.strip() for a in artist_raw_parts if a.strip()]

        return artists, title_part.strip()
    return [], track_str.strip()


def sanitize_local_artist(artist_name: str) -> str:
    # Remove known suffixes like (ofc), (BE), (US), etc.
    return re.sub(r'\((ofc|be|uk|us|fr|it|ca|au|de)\)', '', artist_name, flags=re.IGNORECASE).strip()

def get_local_track_names(folder_path: str) -> List[Tuple[str, str]]:
    tracks = []
    for root, _, files in os.walk(folder_path):
        for file in files:
            if any(file.lower().endswith(ext) for ext in AUDIO_EXTENSIONS):
                name, _ = os.path.splitext(file)
                formatted = re.sub(r'[_\.]', ' ', name).strip()
                tracks.append((formatted, normalize_text(formatted)))
    return tracks


def format_spotify_track(artists: List[str], title: str) -> str:
    # Remove (Original Mix) / (Extended Mix)
    title_clean = re.sub(r'\((Original Mix|Extended Mix)\)', '', title, flags=re.IGNORECASE).strip()

    # Convert dash remix to parentheses
    if ' - ' in title_clean and '(' not in title_clean:
        title_parts = title_clean.split(' - ', 1)
        title_clean = f"{title_parts[0].strip()} ({title_parts[1].strip()})"

    # Detect remixers in title
    remix_match = re.search(r'\((.+? Remix.*?)\)', title_clean, flags=re.IGNORECASE)
    if remix_match:
        remixers_raw = remix_match.group(1)  # e.g., "Kabi (AR) Remix"
        remixers_clean = remixers_raw.replace(' Remix', '').strip()
        # Normalize + strip parentheses for comparison
        remixers_list = [normalize_text(re.sub(r'[()]', '', name.strip())) for name in remixers_clean.split('&')]
    else:
        remixers_list = []

    final_artists = []
    for a in artists:
        norm_a = normalize_text(re.sub(r'[()]', '', a))
        if norm_a not in remixers_list:
            final_artists.append(a)

    # Final formatting
    title_clean = re.sub(r'\s{2,}', ' ', title_clean).strip()
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
        sp_artist_normalized = normalize_text(" ".join(sorted([sanitize_local_artist(a) for a in sp_artists])))
        sp_title_normalized = simplify_title(sp_title, sp_artists)

        found = False

        for local_original, local_normalized in local_tracks:
            if local_original in matched_locals:
                continue  # already matched

            loc_artists, loc_title = extract_artist_and_title(local_original)
            loc_artist_normalized = normalize_text(" ".join(sorted([sanitize_local_artist(a) for a in loc_artists])))
            loc_title_normalized = simplify_title(loc_title, loc_artists)

            artist_score = fuzz.partial_ratio(sp_artist_normalized, loc_artist_normalized)
            title_score = fuzz.partial_ratio(sp_title_normalized, loc_title_normalized)

            if artist_score >= threshold and title_score >= threshold:
                matched.append(sp_track)
                matched_locals.add(local_original)  # mark this specific local file as used
                found = True
                break

        if not found:
            missing.append(sp_track)

    # unmatched = everything not matched
    unmatched_local = [original for original, _ in local_tracks if original not in matched_locals]
    return matched, missing, unmatched_local


def save_list_to_file(data: List[str], path: str, label: str):
    with open(path, "w", encoding="utf-8") as f:
        for item in sorted(data, key=str.lower):
            f.write(item.strip() + "\n")
    print(f"📄 {label} saved to {path}")


def format_local_track_name(raw_name: str) -> str:
    raw_name = clean_filename(raw_name)

    if " - " in raw_name:
        artists, title = raw_name.split(" - ", 1)
        artists = artists.strip()
        title = title.strip()

        # If title ends in a common tag like "Remix", "Edit", "Mix" – wrap in parentheses if not already
        common_suffixes = ['Remix', 'Extended Mix', 'Original Mix', 'Club Mix', 'Radio Edit', 'Edit', 'Dub Mix', 'Version']
        
        # If already has parentheses, assume it’s good
        if not re.search(r'\(.*\)', title):
            for suffix in common_suffixes:
                if title.lower().endswith(suffix.lower()):
                    title = re.sub(rf'{suffix}$', f'({suffix})', title, flags=re.IGNORECASE)
                    break  # stop after first match

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

        # Save results to files
        os.makedirs("logs", exist_ok=True)
        save_list_to_file(missing, "logs/missing_tracks.txt", "Missing tracks")
        save_list_to_file(matched, "logs/matched_tracks.txt", "Matched tracks")
        save_list_to_file(formatted_unmatched, "logs/unmatched_local.txt", "Unmatched local files")


        # Print summary
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
