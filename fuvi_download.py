import os
import time
import re
import itertools
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from difflib import SequenceMatcher
import unidecode

# === CONFIGURATION ===
load_dotenv()
USERNAME = os.getenv("FUVI_USERNAME")
PASSWORD = os.getenv("FUVI_PASSWORD")
PLAYLIST_NAME = "Z"
TRACK_LIST_FILE = "logs/missing_tracks.txt"
FUVI_URL = "https://music.fuvi-clan.com"
CONFIDENCE_THRESHOLD = 0.85

# === DRIVER SETUP ===
def create_driver():
    options = Options()
    options.add_argument("--start-maximized")
    return webdriver.Chrome(options=options)

# === LOGIN ===
def login(driver):
    """Logs into FuviClan using credentials from .env."""
    driver.get(f"{FUVI_URL}/account/login")
    try:
        WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.ID, "email"))).click()
        ActionChains(driver).send_keys(USERNAME).perform()
        WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.ID, "password"))).click()
        ActionChains(driver).send_keys(PASSWORD).perform()
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(text(),'Connexion')]"))).click()
        WebDriverWait(driver, 15).until(EC.presence_of_element_located(
            (By.XPATH, "//a[contains(@href, '/dashboard/download-lists')]")))
        print("✅ Logged in successfully.")
    except Exception as e:
        print("❌ Login failed:", e)
        with open("fuvi_login_debug.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        input("🔍 Check 'fuvi_login_debug.html'. Press Enter to exit.")
        driver.quit()
        exit(1)

# === PLAYLIST CHECK ===
def ensure_playlist_exists(driver, playlist_name):
    """Ensures that the specified playlist exists before proceeding."""
    print("📂 Navigating to download lists...")
    driver.get(f"{FUVI_URL}/dashboard/download-lists")
    try:
        elem = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((
                By.XPATH,
                f"//span[@class='h2 mr-3 w-full break-all hidden md:block' and normalize-space(text())='{playlist_name}']"
            ))
        )
        driver.execute_script("arguments[0].scrollIntoView(true);", elem)
        print(f"✅ Playlist '{playlist_name}' found.")
    except:
        print(f"➕ Playlist '{playlist_name}' not found.")
        input(f"🔧 Create playlist '{playlist_name}' manually, then press Enter to continue...")

# === TEXT UTILS ===
def sanitize_artist_name(name):
    """Removes country tags from artist names."""
    return re.sub(r'\((ofc|be|uk|us|fr|it|ca|au|de)\)', '', name, flags=re.IGNORECASE).strip()

def normalize_text(text: str) -> str:
    """
    Robust normalization:
    - Removes neutral suffixes and country tags
    - Standardizes 'remix', 'extended remix', '&', '+'
    - Converts to ASCII, lowercases, strips punctuation and whitespace
    """
    t = re.sub(r'\s*\[[^\]]+\]\s*$', '', text)
        # Remove neutral suffixes with or without parenthesis, including years and "rework"
    t = re.sub(
        r'\s*\(((?:20\d{2}\s*)?(12[\'"]?\s*version|original (mix|version)|extended (mix|rework)|club mix|extended club mix|radio edit|edit|dub mix|version|rework|remaster(ed)?|mono|stereo))\)\s*$',
        '', t, flags=re.I)
    t = re.sub(
        r'\b((?:20\d{2}\s*)?(12[\'"]?\s*version|original (mix|version)|extended (mix|rework)|club mix|extended club mix|radio edit|edit|dub mix|version|rework|remaster(ed)?|mono|stereo))\b\s*$',
        '', t, flags=re.I)

    t = re.sub(r'\((ofc|be|uk|us|fr|it|ca|au|de)\)', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*[\&\+]\s*', ' and ', t)
    t = re.sub(r'\bextended remix\b', 'remix', t, flags=re.I)
    t = re.sub(r'\bremix\b', 'remix', t, flags=re.I)
    t = unidecode.unidecode(t)
    t = re.sub(r'(?i)\b(feat|ft|featuring)\b', '', t)
    t = t.replace('&', 'and')
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t)
    return t.lower().strip()

def extract_remixers_from_title(title):
    """
    Extracts remixer(s) from titles like '(X Remix)' (X can include multiple names).
    """
    m = re.search(r'\((.+? remix)\)', title, flags=re.I)
    if not m:
        return []
    remixers = m.group(1)
    remixers = re.sub(r'remix', '', remixers, flags=re.I)
    remixers = re.split(r'\s*,\s*|\s+and\s+|\s*&\s*|\s*\+\s*', remixers)
    return [r.strip() for r in remixers if r.strip()]

def generate_artist_permutations(artist_list):
    """Generates all permutations of an artist list as comma-separated strings."""
    if not artist_list:
        return [""]
    if len(artist_list) == 1:
        return [artist_list[0].strip()]
    return [", ".join(p) for p in itertools.permutations([a.strip() for a in artist_list])]

# === MAIN TRACK LOGIC ===
def search_and_add_track(driver, track_name, playlist_name, verbose=True):
    print(f"🔍 Scanning for: {track_name}")

    try:
        driver.get(f"{FUVI_URL}/dashboard/library")

        search_input = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "topbar-search"))
        )
        search_input.clear()
        search_input.send_keys(track_name)
        search_input.send_keys(Keys.RETURN)

        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((
                By.XPATH,
                "//div[@role='rowgroup' and contains(@class, 'w-full')]/div[@role='row']"
            ))
        )
        time.sleep(5)

        result_blocks = driver.find_elements(
            By.XPATH,
            "//div[@role='rowgroup' and contains(@class, 'w-full')]/div[@role='row']"
        )

        # Parse track for matching
        target_artists = track_name.split(" - ")[0] if " - " in track_name else ""
        target_title = track_name.split(" - ")[1] if " - " in track_name else ""

        remixers = extract_remixers_from_title(target_title)
        base_artists_list = [a.strip() for a in target_artists.split(",")] if target_artists else []
        full_artist_list = base_artists_list + [r for r in remixers if r.lower() not in [a.lower() for a in base_artists_list]]

        perms_base = generate_artist_permutations(base_artists_list)
        perms_with_remixers = generate_artist_permutations(full_artist_list)
        all_perms = set(perms_base + perms_with_remixers)

        search_variants = [
            normalize_text(f"{perm} - {target_title}")
            for perm in all_perms
        ]

        best_match = None
        best_score = 0

        for idx, block in enumerate(result_blocks[:3]):
            try:
                title_el = block.find_element(By.XPATH, ".//span[contains(@class, 'h3')]//a")
                title = title_el.text.strip()
                artists_el = block.find_elements(By.XPATH, ".//ul[contains(@class, 'list_virgule')]//a")
                artists = ", ".join(sanitize_artist_name(a.text.strip()) for a in artists_el)
                full_title = f"{artists} - {title}"
                normalized_result = normalize_text(full_title)

                candidate_scores = []
                for variant in search_variants:
                    score = SequenceMatcher(None, variant, normalized_result).ratio()
                    candidate_scores.append((variant, score))

                best_variant, max_score = max(candidate_scores, key=lambda x: x[1])

                if verbose:
                    print(f"🎵 #{idx+1}: {full_title} (score: {round(max_score, 2)})")

                if max_score > best_score:
                    best_score = max_score
                    best_match = block

            except Exception as e:
                print(f"⚠️ Error reading result #{idx+1}: {e}")

        if not best_match or best_score < CONFIDENCE_THRESHOLD:
            print("❌ No suitable match found.")
            return False

        # ✅ Match found — Add to playlist
        try:
            add_btn = best_match.find_element(
                By.XPATH,
                ".//button[contains(@aria-label, 'Ajouter') and contains(@aria-label, 'MP3')]"
            )
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'})", add_btn)
            add_btn.click()

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'modal')]"))
            )

            playlist_items = driver.find_elements(By.XPATH, "//li[contains(@class, 'cursor-pointer')]")

            for li in playlist_items:
                try:
                    label_text = li.text.strip().split("\n")[0]
                    if label_text.strip().lower() == playlist_name.lower():
                        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'})", li)
                        time.sleep(0.5)
                        li.click()
                        print("✅ Track added to playlist.")
                        return True
                except Exception as e:
                    print(f"⚠️ Skipping playlist item due to error: {e}")

            print("❌ Could not find the playlist item in modal.")
            return False

        except Exception as e:
            print(f"⚠️ Couldn't add to playlist: {e}")
            return False

    except Exception as e:
        print(f"❌ Search failed for {track_name}: {e}")
        return False

# === MAIN LOOP ===
def main():
    driver = create_driver()
    login(driver)
    ensure_playlist_exists(driver, PLAYLIST_NAME)

    with open(TRACK_LIST_FILE, "r", encoding="utf-8") as f:
        tracks = [line.strip() for line in f if line.strip()]

    added, not_found = [], []

    for track in tracks:
        if search_and_add_track(driver, track, PLAYLIST_NAME):
            added.append(track)
        else:
            not_found.append(track)

    with open("logs/added_tracks.txt", "w", encoding="utf-8") as f:
        f.writelines(f"{track}\n" for track in added)

    with open("logs/not_found_tracks.txt", "w", encoding="utf-8") as f:
        f.writelines(f"{track}\n" for track in not_found)

    print(f"\n📦 Done. Added: {len(added)}, Not Found: {len(not_found)}")
    print("📁 Files: added_tracks.txt, not_found_tracks.txt")
    input("🔒 Press Enter to close browser...")
    driver.quit()

if __name__ == "__main__":
    main()
