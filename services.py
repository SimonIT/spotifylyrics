import codecs
import json
import os
import re
from urllib import request, parse

import pathvalidate
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
# import unidecode  # NOT NEEDED - only used by chord services (ultimateguitar, cifraclub, songsterr)
from azapi import azapi
from bs4 import BeautifulSoup

# Configure requests with default timeout to prevent hangs
DEFAULT_TIMEOUT = 10  # seconds

class TimeoutHTTPAdapter(HTTPAdapter):
    def __init__(self, timeout=None, *args, **kwargs):
        self.timeout = timeout or DEFAULT_TIMEOUT
        super().__init__(*args, **kwargs)
    
    def send(self, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, **kwargs)

# Apply timeout adapter to all requests
http = requests.Session()
http.mount("http://", TimeoutHTTPAdapter())
http.mount("https://", TimeoutHTTPAdapter())
# Replace requests.get with session get that has default timeout
requests_get_original = requests.get
requests.get = http.get

try:
    import spotify_lyric.crawlers.QQCrawler as QQCrawler
    import spotify_lyric.model_traditional_conversion.langconv as langconv
except ModuleNotFoundError:
    pass


class Config:
    ERROR = "Error: Could not find lyrics."
    PROXY = request.getproxies()

    if os.name == "nt":
        SETTINGS_DIR = os.getenv("APPDATA") + "\\SpotifyLyrics\\"
    else:
        SETTINGS_DIR = os.path.expanduser("~") + "/.SpotifyLyrics/"
    DEFAULT_LYRICS_DIR = os.path.join(SETTINGS_DIR, "lyrics")
    LYRICS_DIR = DEFAULT_LYRICS_DIR


UA = "Mozilla/5.0 (Maemo; Linux armv7l; rv:10.0.1) Gecko/20100101 Firefox/10.0.1 Fennec/10.0.1"


def _local(song):
    service_name = "Local"
    url = ""
    timed = False
    lyrics = Config.ERROR

    if os.path.isdir(Config.LYRICS_DIR):
        path_song_name = pathvalidate.sanitize_filename(song.name.lower())
        path_artist_name = pathvalidate.sanitize_filename(song.artist.lower())
        for file in os.listdir(Config.LYRICS_DIR):
            file = os.path.join(Config.LYRICS_DIR, file)
            if os.path.isfile(file):
                file_parts = os.path.splitext(file)
                file_extension = file_parts[1].lower()
                if file_extension in (".txt", ".lrc"):
                    file_name = file_parts[0].lower()
                    if path_song_name in file_name and path_artist_name in file_name:
                        with open(file, "r", encoding="UTF-8") as lyrics_file:
                            lyrics = lyrics_file.read()
                        timed = file_extension == ".lrc"
                        url = "file:///" + os.path.abspath(file)
                        break

    return lyrics, url, service_name, timed


def _rentanadviser(song):
    service_name = "RentAnAdviser"
    url = ""

    search_url = "https://www.rentanadviser.com/en/subtitles/subtitles4songs.aspx?%s" % parse.urlencode({
        "src": song.artist + " " + song.name
    })
    try:
        search_results = requests.get(search_url, proxies=Config.PROXY)
        soup = BeautifulSoup(search_results.text, 'html.parser')
        result_links = soup.find(id="tablecontainer").find_all("a")

        for result_link in result_links:
            if result_link["href"] != "subtitles4songs.aspx":
                lower_title = result_link.get_text().lower()
                if song.artist.lower() in lower_title and song.name.lower() in lower_title:
                    url = "https://www.rentanadviser.com/en/subtitles/%s&type=lrc" % result_link["href"]
                    break

        if url:
            possible_text = requests.get(url, proxies=Config.PROXY)
            soup = BeautifulSoup(possible_text.text, 'html.parser')

            event_validation = soup.find(id="__EVENTVALIDATION")["value"]
            view_state = soup.find(id="__VIEWSTATE")["value"]

            lrc = requests.post(url, {"__EVENTTARGET": "ctl00$ContentPlaceHolder1$btnlyrics",
                                      "__EVENTVALIDATION": event_validation,
                                      "__VIEWSTATE": view_state}, proxies=Config.PROXY).text

            return lrc, url, service_name, True

    except Exception as error:
        print("%s: %s" % (service_name, error))
    return Config.ERROR, url, service_name, False


def _megalobiz(song):
    service_name = "Megalobiz"
    url = ""

    search_url = "https://www.megalobiz.com/search/all?%s" % parse.urlencode({
        "qry": song.artist + " " + song.name,
        "display": "more"
    })
    try:
        search_results = requests.get(search_url, proxies=Config.PROXY, timeout=10)
        soup = BeautifulSoup(search_results.text, 'html.parser')
        
        container = soup.find(id="list_entity_container")
        if not container:
            return Config.ERROR, url, service_name, False
            
        result_links = container.find_all("a", class_="entity_name")

        for result_link in result_links:
            lower_title = result_link.get_text().lower()
            if song.artist.lower() in lower_title and song.name.lower() in lower_title:
                url = "https://www.megalobiz.com%s" % result_link["href"]
                break

        if url:
            possible_text = requests.get(url, proxies=Config.PROXY, timeout=10)
            soup = BeautifulSoup(possible_text.text, 'html.parser')

            lyrics_div = soup.find("div", class_="lyrics_details")
            if lyrics_div and lyrics_div.span:
                lrc = lyrics_div.span.get_text()
                return lrc, url, service_name, True

    except Exception as error:
        print("%s: %s" % (service_name, error))
    return Config.ERROR, url, service_name, False


def _qq(song):
    url = ""
    try:
        qq = QQCrawler.QQCrawler()
        sid = qq.getSongId(artist=song.artist, song=song.name)
        url = qq.getLyticURI(sid)
    except Exception as error:
        print("%s: %s" % ("QQ", error))
        return Config.ERROR, url, "QQ", False

    lrc_string = ""
    for line in requests.get(url, proxies=Config.PROXY).text.splitlines():
        line_text = line.split(']')
        lrc_string += "]".join(line_text[:-1]) + langconv.Converter('zh-hant').convert(line_text)

    return lrc_string, url, qq.name, True


def _syair(song):
    service_name = "Syair"
    url = ""

    search_url = "https://www.syair.info/search?%s" % parse.urlencode({
        "q": song.artist + " " + song.name
    })
    try:
        search_results = requests.get(search_url, proxies=Config.PROXY, headers={"User-Agent": UA})
        soup = BeautifulSoup(search_results.text, 'html.parser')

        result_container = soup.find("div", class_="sub")

        if result_container:
            result_list = result_container.find_all("div", class_="li")

            if result_list:
                for result in result_list:
                    result_link = result.find("a")
                    name = result_link.get_text().lower()
                    if song.artist.lower() in name and song.name.lower() in name:
                        url = "https://www.syair.info%s" % result_link["href"]
                        break

                if url:
                    lyrics_page = requests.get(url, proxies=Config.PROXY, headers={"User-Agent": UA})
                    soup = BeautifulSoup(lyrics_page.text, 'html.parser')
                    lrc_link = ""
                    for download_link in soup.find_all("a"):
                        if "download.php" in download_link["href"]:
                            lrc_link = download_link["href"]
                            break
                    if lrc_link:
                        lrc = requests.get("https://www.syair.info%s" % lrc_link, proxies=Config.PROXY,
                                           cookies=lyrics_page.cookies, headers={"User-Agent": UA}).text

                        return lrc, url, service_name, True
    except Exception as error:
        print("%s: %s" % (service_name, error))
    return Config.ERROR, url, service_name, False


def _musixmatch(song):
    service_name = "Musixmatch"
    url = ""
    lyrics = Config.ERROR

    def extract_mxm_props(soup_page):
        scripts = soup_page.find_all("script")
        props_script = None
        for script in scripts:
            if script and script.contents and len(script.contents) > 0 and "__mxmProps" in script.contents[0]:
                props_script = script
                break
        if props_script and props_script.contents:
            return props_script.contents[0]
        return ""

    try:
        search_url = "https://www.musixmatch.com/search/%s-%s/tracks" % (
            song.artist.replace(' ', '-'), song.name.replace(' ', '-'))
        header = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        search_results = requests.get(search_url, headers=header, proxies=Config.PROXY, timeout=10)
        soup = BeautifulSoup(search_results.text, 'html.parser')
        
        props_content = extract_mxm_props(soup)
        if props_content:
            page = re.findall('"track_share_url":"([^"]*)', props_content)
            if page:
                url = codecs.decode(page[0], 'unicode-escape')
                lyrics_page = requests.get(url, headers=header, proxies=Config.PROXY, timeout=10)
                soup = BeautifulSoup(lyrics_page.text, 'html.parser')
                props = extract_mxm_props(soup)
                if props and '"body":"' in props:
                    lyrics = props.split('"body":"')[1].split('","language"')[0]
                    lyrics = lyrics.replace("\\n", "\n")
                    lyrics = lyrics.replace("\\", "")
                    if not lyrics.strip():
                        lyrics = Config.ERROR
                    else:
                        album = soup.find(class_="mxm-track-footer__album")
                        if album:
                            album_title = album.find(class_="mui-cell__title")
                            if album_title:
                                song.album = album_title.getText()
    except Exception as error:
        print("%s: %s" % (service_name, error))
    return lyrics, url, service_name


def _songmeanings(song):
    service_name = "Songmeanings"
    url = ""
    lyrics = Config.ERROR
    try:
        search_url = "http://songmeanings.com/m/query/?q=%s %s" % (song.artist, song.name)
        search_results = requests.get(search_url, proxies=Config.PROXY, timeout=10)
        soup = BeautifulSoup(search_results.text, 'html.parser')
        url = ""
        for link in soup.find_all('a', href=True):
            if "songmeanings.com/m/songs/view/" in link['href']:
                url = "https:" + link['href']
                break
            elif "/m/songs/view/" in link['href']:
                result = "http://songmeanings.com" + link['href']
                lyrics_page = requests.get(result, proxies=Config.PROXY, timeout=10)
                soup = BeautifulSoup(lyrics_page.text, 'html.parser')
                url = "http://songmeanings.com" + link['href'][2:]
                break
        lis = soup.find_all('ul', attrs={'data-inset': True})
        if len(lis) > 1:
            li_items = lis[1].find_all('li')
            if len(li_items) > 1:
                temp_lyrics = li_items[1]
                lyrics = temp_lyrics.getText()
    except Exception as error:
        print("%s: %s" % (service_name, error))
    if lyrics == "We are currently missing these lyrics.":
        lyrics = Config.ERROR

    # lyrics = lyrics.encode('cp437', errors='replace').decode('utf-8', errors='replace')
    return lyrics, url, service_name


def _songlyrics(song):
    service_name = "Songlyrics"
    url = ""
    try:
        artistm = song.artist.replace(" ", "-")
        songm = song.name.replace(" ", "-")
        url = "https://www.songlyrics.com/%s/%s-lyrics" % (artistm, songm)
        lyrics_page = requests.get(url, proxies=Config.PROXY, timeout=10)
        soup = BeautifulSoup(lyrics_page.text, 'html.parser')
        
        lyrics_div = soup.find(id="songLyricsDiv")
        if lyrics_div:
            lyrics = lyrics_div.get_text()
            if "Sorry, we have no" in lyrics or "We do not have" in lyrics:
                lyrics = Config.ERROR
            else:
                pagetitle = soup.find("div", class_="pagetitle")
                if pagetitle:
                    for info in pagetitle.find_all("p"):
                        if "Album:" in info.get_text():
                            album_link = info.find("a")
                            if album_link:
                                song.album = album_link.get_text()
        else:
            lyrics = Config.ERROR
    except Exception as error:
        print("%s: %s" % (service_name, error))
        lyrics = Config.ERROR
    return lyrics, url, service_name


def _genius(song):
    service_name = "Genius"
    url = ""
    lyrics = Config.ERROR
    try:
        # Updated to use the newer Genius HTML structure
        url = "https://genius.com/%s-%s-lyrics" % (song.artist.replace(' ', '-'), song.name.replace(' ', '-'))
        header = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
        lyrics_page = requests.get(url, proxies=Config.PROXY, headers=header, timeout=10)
        soup = BeautifulSoup(lyrics_page.text, 'html.parser')
        
        # Try multiple possible div structures used by Genius
        lyrics_divs = soup.find_all("div", {"data-lyrics-container": "true"})
        if lyrics_divs:
            lyrics_parts = []
            for div in lyrics_divs:
                lyrics_parts.append(div.get_text(separator="\n"))
            lyrics = "\n".join(lyrics_parts)
            
            # Clean up Genius-specific artifacts
            lyrics = lyrics.strip()
            # Remove [Intro], [Verse], [Chorus] markers if desired
            # lyrics = re.sub(r'\[.*?\]', '', lyrics)
        else:
            # Fallback to old method
            lyrics_container = soup.find("div", {"class": "lyrics"})
            if lyrics_container:
                lyrics = lyrics_container.get_text()
        
        if lyrics and lyrics != Config.ERROR and lyrics.strip():
            # Verify it's the right song by checking if artist appears somewhere
            if song.artist.lower().replace(" ", "") not in soup.text.lower().replace(" ", ""):
                lyrics = Config.ERROR
        else:
            lyrics = Config.ERROR
    except Exception as error:
        print("%s: %s" % (service_name, error))
    return lyrics, url, service_name


def _versuri(song):
    service_name = "Versuri"
    url = ""
    lyrics = Config.ERROR
    try:
        search_url = "https://www.versuri.ro/q/%s+%s/" % \
                     (song.artist.replace(" ", "+").lower(), song.name.replace(" ", "+").lower())
        search_results = requests.get(search_url, proxies=Config.PROXY, timeout=10)
        soup = BeautifulSoup(search_results.text, 'html.parser')
        for search_results in soup.findAll('a'):
            if "/versuri/" in search_results['href']:
                link_text = search_results.getText().lower()
                if song.artist.lower() in link_text and song.name.lower() in link_text:
                    url = "https://www.versuri.ro" + search_results['href']
                    break
        if not url:
            lyrics = Config.ERROR
        else:
            lyrics_page = requests.get(url, proxies=Config.PROXY, timeout=10)
            soup = BeautifulSoup(lyrics_page.text, 'html.parser')
            content = soup.find_all('div', {'id': 'pagecontent'})[0]
            lyrics = str(content)[str(content).find("</script><br/>") + 14:str(content).find("<br/><br/><center>")]
            lyrics = lyrics.replace("<br/>", "")
        if "nu există" in lyrics:
            lyrics = Config.ERROR
    except Exception as error:
        print("%s: %s" % (service_name, error))
        lyrics = Config.ERROR
    return lyrics, url, service_name


def _azapi(song):
    service = "Azapi"

    try:
        try:
            # azapi doesn't accept timeout parameter
            api = azapi.AZlyrics('duckduckgo', accuracy=0.5)
        except Exception:
            try:
                api = azapi.AZlyrics('google', accuracy=0.5)
            except Exception:
                return Config.ERROR, "", service

        if not song.artist:
            return Config.ERROR, "", service

        api.artist = song.artist
        api.title = song.name

        songs = api.getSongs()

        if song.name in songs:
            result_song = songs[song.name]
        else:
            return Config.ERROR, "", service

        if result_song.get("album"):
            song.album = result_song["album"]
        if result_song.get("year"):
            song.year = int(result_song["year"])

        lyrics = api.getLyrics(url=result_song["url"])
        
        if not isinstance(lyrics, str) or not lyrics.strip():
            return Config.ERROR, "", service
            
        return lyrics, result_song["url"], service
    except Exception as error:
        print("%s: %s" % (service, error))
        return Config.ERROR, "", service


def _lyricsovh(song):
    """Fetch lyrics from lyrics.ovh API - a free and reliable lyrics API"""
    service_name = "Lyrics.ovh"
    lyrics = Config.ERROR
    url = ""
    
    try:
        # lyrics.ovh API endpoint
        api_url = "https://api.lyrics.ovh/v1/%s/%s" % (
            parse.quote(song.artist), parse.quote(song.name)
        )
        
        response = requests.get(api_url, proxies=Config.PROXY, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if "lyrics" in data and data["lyrics"]:
                lyrics = data["lyrics"].strip()
                url = api_url
        
    except Exception as error:
        print("%s: %s" % (service_name, error))
    
    return lyrics, url, service_name


def _chartlyrics(song):
    """Fetch lyrics from ChartLyrics API - another free lyrics API"""
    service_name = "ChartLyrics"
    lyrics = Config.ERROR
    url = ""
    
    try:
        # ChartLyrics API endpoint
        api_url = "http://api.chartlyrics.com/apiv1.asmx/SearchLyricDirect?%s" % parse.urlencode({
            "artist": song.artist,
            "song": song.name
        })
        
        response = requests.get(api_url, proxies=Config.PROXY, timeout=10)
        
        if response.status_code == 200:
            import xmltodict
            data = xmltodict.parse(response.text)
            
            if "GetLyricResult" in data:
                result = data["GetLyricResult"]
                if result.get("Lyric"):
                    lyrics = result["Lyric"].strip()
                    if lyrics and lyrics != "":
                        url = result.get("LyricUrl", api_url)
        
    except Exception as error:
        print("%s: %s" % (service_name, error))
    
    return lyrics, url, service_name


def _tekstowo(song):
    """Fetch lyrics from Tekstowo.pl - large Polish/international lyrics database"""
    service_name = "Tekstowo"
    lyrics = Config.ERROR
    url = ""
    
    try:
        # Search for the song
        search_url = "https://www.tekstowo.pl/szukaj.html"
        search_data = {"search-artist": song.artist, "search-title": song.name}
        
        response = requests.post(search_url, data=search_data, proxies=Config.PROXY, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find first result
        results = soup.find_all("a", class_="title")
        if results:
            song_url = "https://www.tekstowo.pl" + results[0]["href"]
            
            # Get lyrics page
            lyrics_page = requests.get(song_url, proxies=Config.PROXY, timeout=10)
            soup = BeautifulSoup(lyrics_page.text, 'html.parser')
            
            lyrics_div = soup.find("div", class_="song-text")
            if lyrics_div:
                lyrics = lyrics_div.get_text(separator="\n").strip()
                url = song_url
    
    except Exception as error:
        print("%s: %s" % (service_name, error))
    
    return lyrics, url, service_name


def _letras(song):
    """Fetch lyrics from Letras.mus.br - Brazilian lyrics site with international coverage"""
    service_name = "Letras"
    lyrics = Config.ERROR
    url = ""
    
    try:
        # Build URL - Letras uses artist/song format
        artist_slug = song.artist.lower().replace(" ", "-").replace(".", "")
        song_slug = song.name.lower().replace(" ", "-").replace(".", "")
        url = f"https://www.letras.mus.br/{artist_slug}/{song_slug}/"
        
        response = requests.get(url, proxies=Config.PROXY, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            lyrics_div = soup.find("div", class_="lyric-original")
            
            if lyrics_div:
                lyrics = lyrics_div.get_text(separator="\n").strip()
    
    except Exception as error:
        print("%s: %s" % (service_name, error))
    
    return lyrics, url, service_name


def _lyricscom(song):
    """Fetch lyrics from Lyrics.com - large commercial lyrics database"""
    service_name = "Lyrics.com"
    lyrics = Config.ERROR
    url = ""
    
    try:
        # Lyrics.com requires proper headers to avoid 403
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.lyrics.com/'
        }
        
        # Search for song
        search_url = "https://www.lyrics.com/serp.php?%s" % parse.urlencode({
            "st": f"{song.artist} {song.name}",
            "qtype": "2"
        })
        
        response = requests.get(search_url, headers=headers, proxies=Config.PROXY, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find first result
        results = soup.find_all("a", href=lambda x: x and "/lyric/" in x)
        if results:
            url = "https://www.lyrics.com" + results[0]["href"]
            
            # Get lyrics page
            lyrics_page = requests.get(url, headers=headers, proxies=Config.PROXY, timeout=10)
            soup = BeautifulSoup(lyrics_page.text, 'html.parser')
            
            lyrics_div = soup.find("pre", id="lyric-body-text")
            if lyrics_div:
                lyrics = lyrics_div.get_text().strip()
    
    except Exception as error:
        print("%s: %s" % (service_name, error))
    
    return lyrics, url, service_name


def _azlyrics(song):
    """Direct scraper for AZLyrics.com - comprehensive lyrics database"""
    service_name = "AZLyrics"
    lyrics = Config.ERROR
    url = ""
    
    try:
        # AZLyrics requires specific URL format: lowercase, no spaces, no punctuation
        artist = re.sub(r'[^a-z0-9]', '', song.artist.lower())
        title = re.sub(r'[^a-z0-9]', '', song.name.lower())
        
        # Remove common prefixes
        if artist.startswith('the'):
            artist = artist[3:]
        
        url = f"https://www.azlyrics.com/lyrics/{artist}/{title}.html"
        
        # AZLyrics blocks requests without proper headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.azlyrics.com/'
        }
        
        response = requests.get(url, headers=headers, proxies=Config.PROXY, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # AZLyrics has lyrics in a div without class/id, right after comment <!-- Usage of azlyrics.com content -->
            comments = soup.find_all(string=lambda text: isinstance(text, str) and 'Usage of azlyrics.com content' in text)
            if comments:
                lyrics_div = comments[0].find_next('div')
                if lyrics_div:
                    lyrics_text = lyrics_div.get_text().strip()
                    # Only accept if we actually got lyrics (not empty)
                    if lyrics_text and len(lyrics_text) > 50:  # Minimum lyrics length
                        lyrics = lyrics_text
    
    except Exception as error:
        print("%s: %s" % (service_name, error))
    
    return lyrics, url, service_name


def _lyricalnonsense(song):
    """Lyrical Nonsense - Excellent for Japanese songs with romaji/translation"""
    service_name = "Lyrical-Nonsense"
    lyrics = Config.ERROR
    url = ""
    
    try:
        # Search for the song
        search_url = "https://www.lyrical-nonsense.com/global/search/"
        params = {
            'q': f"{song.artist} {song.name}"
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        search_response = requests.get(search_url, params=params, headers=headers, proxies=Config.PROXY, timeout=10)
        soup = BeautifulSoup(search_response.text, 'html.parser')
        
        # Find first result link
        result_link = soup.find('a', href=lambda x: x and '/lyrics/' in x)
        if result_link:
            href = result_link['href']
            # Check if href is relative or absolute
            if href.startswith('http'):
                url = href
            else:
                url = "https://www.lyrical-nonsense.com" + href
            
            # Get lyrics page
            lyrics_response = requests.get(url, headers=headers, proxies=Config.PROXY, timeout=10)
            lyrics_soup = BeautifulSoup(lyrics_response.text, 'html.parser')
            
            # Try to get romaji version first (for Japanese songs)
            romaji_div = lyrics_soup.find('div', id='Romaji')
            if romaji_div:
                lyrics_pre = romaji_div.find('pre', class_='olyrictext')
                if lyrics_pre:
                    lyrics = lyrics_pre.get_text().strip()
            
            # If no romaji, try original lyrics
            if lyrics == Config.ERROR:
                original_div = lyrics_soup.find('div', id='Original')
                if original_div:
                    lyrics_pre = original_div.find('pre', class_='olyrictext')
                    if lyrics_pre:
                        lyrics = lyrics_pre.get_text().strip()
    
    except Exception as error:
        print("%s: %s" % (service_name, error))
    
    return lyrics, url, service_name


def _geniusromaji(song):
    """Try to get romanized version from Genius (for Japanese/Korean songs)"""
    service_name = "Genius-Romaji"
    lyrics = Config.ERROR
    url = ""
    
    try:
        # Search with "romanized" keyword
        search_url = "https://genius.com/api/search/multi?q=%s" % parse.quote(f"{song.artist} {song.name} romanized")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }
        
        response = requests.get(search_url, headers=headers, proxies=Config.PROXY, timeout=10)
        data = response.json()
        
        # Find song with "Romanized" in title
        if 'response' in data and 'sections' in data['response']:
            for section in data['response']['sections']:
                if section.get('type') == 'song':
                    for hit in section.get('hits', []):
                        result = hit.get('result', {})
                        title = result.get('title', '')
                        if 'romanized' in title.lower() or 'romaji' in title.lower():
                            url = result.get('url', '')
                            break
                    if url:
                        break
        
        # Get lyrics from the romanized version page
        if url:
            lyrics_response = requests.get(url, headers={'User-Agent': headers['User-Agent']}, 
                                          proxies=Config.PROXY, timeout=10)
            soup = BeautifulSoup(lyrics_response.text, 'html.parser')
            
            # Try new Genius format with data-lyrics-container
            lyrics_containers = soup.find_all('div', attrs={'data-lyrics-container': 'true'})
            if lyrics_containers:
                lyrics_parts = []
                for container in lyrics_containers:
                    lyrics_parts.append(container.get_text(separator='\n').strip())
                lyrics = '\n\n'.join(lyrics_parts)
    
    except Exception as error:
        print("%s: %s" % (service_name, error))
    
    return lyrics, url, service_name


# tab/chord services

def _ultimateguitar(song):
    artist = unidecode.unidecode(song.artist)
    title = unidecode.unidecode(song.name)
    url_pt1 = 'https://www.ultimate-guitar.com/search.php?view_state=advanced&band_name='
    url_pt2 = '&song_name='
    url_pt3 = '&type%5B%5D=300&type%5B%5D=200&rating%5B%5D=5&version_la='
    # song = song.replace('-', '+')
    # artist = artist.replace('-', '+')
    url = url_pt1 + artist + url_pt2 + title + url_pt3
    page = requests.get(url)

    if page.status_code == 200:
        soup = BeautifulSoup(page.content, 'html.parser')

        search_results_element = soup.find_all('div', {'class': 'js-store'})[0]
        search_results_data = json.loads(search_results_element["data-content"])

        urls = []
        data = search_results_data["store"]["page"]["data"]
        if "results" in data.keys():
            for result in data["results"]:
                urls.append(result["tab_url"])

        return urls
    return []


def _cifraclub(song):
    artist = unidecode.unidecode(song.artist)
    title = unidecode.unidecode(song.name)
    url = 'https://www.cifraclub.com.br/{}/{}'.format(artist.replace(" ", "-").lower(), title.replace(" ", "-").lower())

    try:
        result = requests.get(url, proxies=Config.PROXY)
    except Exception as error:
        print("cifraclub: %s" % error)
        return []

    if result.status_code == 200:
        return [result.url]
    else:
        return []


# don't even get to this point, but it's an option for source
# just got to change services_list3 list order
def _songsterr(song):
    artist = unidecode.unidecode(song.artist)
    title = unidecode.unidecode(song.name)
    url = 'http://www.songsterr.com/a/wa/bestMatchForQueryString?s={}&a={}'.format(title, artist)
    return [url]


def _tanzmusikonline(song):
    try:
        token_request = requests.get('https://www.tanzmusik-online.de/search', timeout=30)
        search = BeautifulSoup(token_request.content, 'html.parser').find(id="page-wrapper")
        if search:
            token = ""
            for input_field in search.find("form").find_all("input"):
                if input_field.get("name") == "_token":
                    token = input_field.get("value")
                    break
            page = 1
            highest_page = 2
            song_urls = []
            base_result_url = 'https://www.tanzmusik-online.de/search/result'
            while page < highest_page:
                search_results = requests.post(base_result_url + "?page=" + str(page), proxies=Config.PROXY,
                                               cookies=token_request.cookies,
                                               data={"artist": song.artist, "song": song.name, "_token": token,
                                                     "searchMode": "extended", "genre": 0, "submit": "Suchen"},
                                               timeout=30)
                search_soup = BeautifulSoup(search_results.content, 'html.parser')
                for song_result in search_soup.find_all(class_="song"):
                    song_urls.append(song_result.find(class_="songTitle").a.get("href"))
                if page == 1:
                    pagination = search_soup.find(class_="pagination")
                    if pagination:
                        for page_number_element in pagination.find_all("a"):
                            page_number = page_number_element.getText()
                            if page_number.isdigit():
                                highest_page = int(page_number) + 1
                page += 1

            language = requests.get("https://www.tanzmusik-online.de/locale/en", proxies=Config.PROXY, timeout=30)
            for song_url in song_urls:
                page = requests.get(song_url, proxies=Config.PROXY, cookies=language.cookies, timeout=30)

                soup = BeautifulSoup(page.content, 'html.parser')

                for dance in soup.find(class_="dances").find_all("div"):
                    dance_name = dance.a.getText().strip().replace("Disco Fox", "Discofox")
                    if dance_name not in song.dances:
                        song.dances.append(dance_name)

                details = soup.find(class_="songDetails")
                if details:
                    for detail in details.find_all(class_="line"):
                        classes = detail.i.get("class")
                        typ, text = detail.div.getText().split(":", 1)
                        if "fa-dot-circle-o" in classes:
                            if typ.strip().lower() == "album":
                                song.album = text.strip()
                        elif "fa-calendar-o" in classes:
                            song.year = int(text)
                        elif "fa-flag" in classes:
                            song.genre = text.strip()
                        elif "fa-music" in classes:
                            song.cycles_per_minute = int(text)
                        elif "fa-tachometer" in classes:
                            song.beats_per_minute = int(text)
    except Exception as error:
        print("%s: %s" % ("Tanzmusik Online", error))


def _welchertanz(song):
    try:
        interpreter_request = requests.get("https://tanzschule-woelbing.de/charts/interpreten/", proxies=Config.PROXY)
        interpreter_soup = BeautifulSoup(interpreter_request.content, 'html.parser')
        interpreter_links = []
        for interpreter in interpreter_soup.find_all("a", class_="btn-dfeault"):
            if "/charts/interpreten/?artist-hash=" in interpreter.get("href") \
                    and song.artist.lower() in interpreter.getText().lower():
                interpreter_links.append(interpreter.get("href"))
        for interpreter_link in interpreter_links:
            interpreter_songs = requests.get("https://tanzschule-woelbing.de" + interpreter_link, proxies=Config.PROXY)
            interpreter_songs_soup = BeautifulSoup(interpreter_songs.content, 'html.parser')
            for interpreter_song in interpreter_songs_soup.find("table", class_="table").find_all("tr"):
                infos = interpreter_song.find_all("td")
                if infos and song.name.lower() in infos[1].getText().strip().lower():
                    dances = infos[2].find_all("a")
                    for dance in dances:
                        dance_name = dance.getText().strip() \
                            .replace("Cha-Cha-Cha", "Cha Cha Cha") \
                            .replace("Wiener", "Viennese") \
                            .replace("Walzer", "Waltz") \
                            .replace("Foxtrott", "Foxtrot")
                        if dance_name != "---" and dance_name not in song.dances:
                            song.dances.append(dance_name)
    except Exception as error:
        print("%s: %s" % ("Tanzschule Woelbing", error))
