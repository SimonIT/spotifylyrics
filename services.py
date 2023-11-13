import codecs
import functools
import json
import os
import re
from urllib import request, parse

import pathvalidate
import requests
import unidecode  # to remove accents
from azapi import azapi
from bs4 import BeautifulSoup
from sentry_sdk import capture_exception

try:
    import spotify_lyric.crawlers.QQCrawler as QQCrawler
    import spotify_lyric.model_traditional_conversion.langconv as langconv
except ModuleNotFoundError:
    pass

# With Sync.
SERVICES_LIST1 = []

# Without Sync.
SERVICES_LIST2 = []


class Config:
    PROXY = request.getproxies()

    if os.name == "nt":
        SETTINGS_DIR = f"{os.getenv('APPDATA')}\\SpotifyLyrics\\"
    else:
        SETTINGS_DIR = f"{os.path.expanduser('~')}/.SpotifyLyrics/"
    DEFAULT_LYRICS_DIR = os.path.join(SETTINGS_DIR, "lyrics")
    LYRICS_DIR = DEFAULT_LYRICS_DIR


UA = "Mozilla/5.0 (Maemo; Linux armv7l; rv:10.0.1) Gecko/20100101 Firefox/10.0.1 Fennec/10.0.1"


def lyrics_service(_func=None, *, synced=False, enabled=True):
    def _decorator_lyrics_service(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except requests.exceptions.RequestException as error:
                print("%s: %s" % (func.__name__, error))
            except Exception as e:
                capture_exception(e)

        if enabled:
            if synced:
                SERVICES_LIST1.append(wrapper)
            else:
                SERVICES_LIST2.append(wrapper)
        return wrapper

    if _func is None:
        return _decorator_lyrics_service
    else:
        return _decorator_lyrics_service(_func)


@lyrics_service(synced=True)
def _local(song):
    service_name = "Local"

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
                        url = f"file:///{os.path.abspath(file)}"
                        return lyrics, url, service_name, timed


@lyrics_service(synced=True)
def _rentanadviser(song):
    service_name = "RentAnAdviser"

    search_url = "https://www.rentanadviser.com/en/subtitles/subtitles4songs.aspx?%s" % parse.urlencode({
        "src": f"{song.artist} {song.name}"
    })
    search_results = requests.get(search_url, proxies=Config.PROXY, headers={"User-Agent": UA})
    soup = BeautifulSoup(search_results.text, 'html.parser')
    result_links = soup.find(id="tablecontainer").find_all("a")

    for result_link in result_links:
        if result_link["href"] != "subtitles4songs.aspx":
            lower_title = result_link.get_text().lower()
            if song.artist.lower() in lower_title and song.name.lower() in lower_title:
                url = f'https://www.rentanadviser.com/en/subtitles/{result_link["href"]}&type=lrc'
                possible_text = requests.get(url, proxies=Config.PROXY, headers={"User-Agent": UA})
                soup = BeautifulSoup(possible_text.text, 'html.parser')

                event_validation = soup.find(id="__EVENTVALIDATION")["value"]
                view_state = soup.find(id="__VIEWSTATE")["value"]

                lrc = requests.post(possible_text.url,
                                    {"__EVENTTARGET": "ctl00$ContentPlaceHolder1$btnlyrics",
                                     "__EVENTVALIDATION": event_validation,
                                     "__VIEWSTATE": view_state},
                                    headers={"User-Agent": UA, "referer": possible_text.url},
                                    proxies=Config.PROXY,
                                    cookies=search_results.cookies)

                return lrc.text, possible_text.url, service_name, True


@lyrics_service(synced=True)
def _megalobiz(song):
    service_name = "Megalobiz"

    search_url = "https://www.megalobiz.com/search/all?%s" % parse.urlencode({
        "qry": f"{song.artist} {song.name}",
        "display": "more"
    })
    search_results = requests.get(search_url, proxies=Config.PROXY)
    soup = BeautifulSoup(search_results.text, 'html.parser')
    result_links = soup.find(id="list_entity_container").find_all("a", class_="entity_name")

    for result_link in result_links:
        lower_title = result_link.get_text().lower()
        if song.artist.lower() in lower_title and song.name.lower() in lower_title:
            url = f"https://www.megalobiz.com{result_link['href']}"
            possible_text = requests.get(url, proxies=Config.PROXY)
            soup = BeautifulSoup(possible_text.text, 'html.parser')

            lrc = soup.find("div", class_="lyrics_details").span.get_text()

            return lrc, possible_text.url, service_name, True


@lyrics_service(synced=True, enabled=False)
def _qq(song):
    qq = QQCrawler.QQCrawler()
    sid = qq.getSongId(artist=song.artist, song=song.name)
    url = qq.getLyticURI(sid)

    lrc_string = ""
    for line in requests.get(url, proxies=Config.PROXY).text.splitlines():
        line_text = line.split(']')
        lrc_string += "]".join(line_text[:-1]) + langconv.Converter('zh-hant').convert(line_text)

    return lrc_string, url, qq.name, True


@lyrics_service(synced=True)
def _lyricsify(song):
    service_name = "Lyricsify"

    search_url = "https://www.lyricsify.com/search?%s" % parse.urlencode({
        "q": f"{song.artist} {song.name}"
    })
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
                    url = f"https://www.lyricsify.com{result_link['href']}?download"
                    lyrics_page = requests.get(url, proxies=Config.PROXY, headers={"User-Agent": UA})
                    soup = BeautifulSoup(lyrics_page.text, 'html.parser')

                    download_link = soup.find(id="iframe_download")["src"]
                    lrc = requests.get(download_link, proxies=Config.PROXY,
                                       cookies=lyrics_page.cookies, headers={"User-Agent": UA}).text
                    return lrc, lyrics_page.url, service_name, True


@lyrics_service(synced=True)
def _rclyricsband(song):
    service_name = "RC Lyrics Band"
    search_results = requests.get("https://rclyricsband.com/", params={"s": "%s %s" % (song.artist, song.name)},
                                  proxies=Config.PROXY)
    search_soup = BeautifulSoup(search_results.text, 'html.parser')

    for result in search_soup.find(id="main").find_all("article"):
        title_link = result.find(class_="elementor-post__title").find("a")
        lower_title = title_link.get_text().lower()
        if song.artist.lower() in lower_title and song.name.lower() in lower_title:
            song_page = requests.get(title_link["href"])
            song_page_soup = BeautifulSoup(song_page.text, 'html.parser')
            lrc_download_button = song_page_soup.find(lambda tag: tag.name == "a" and "LRC Download" in tag.text)
            lyrics = requests.get(lrc_download_button["href"]).text
            return lyrics, song_page.url, service_name, True


@lyrics_service
def _musixmatch(song):
    service_name = "Musixmatch"

    def extract_mxm_props(soup_page):
        scripts = soup_page.find_all("script")
        for script in scripts:
            if script and script.contents and "__mxmProps" in script.contents[0]:
                return script.contents[0]

    search_url = "https://www.musixmatch.com/search/%s-%s" % (
        song.artist.replace(' ', '-'), song.name.replace(' ', '-'))
    header = {"User-Agent": "curl/7.9.8 (i686-pc-linux-gnu) libcurl 7.9.8 (OpenSSL 0.9.6b) (ipv6 enabled)"}
    search_results = requests.get(search_url, headers=header, proxies=Config.PROXY)
    soup = BeautifulSoup(search_results.text, 'html.parser')
    props = extract_mxm_props(soup)
    if props:
        page = re.findall('"track_share_url":"([^"]*)', props)
        if page:
            url = codecs.decode(page[0], 'unicode-escape')
            lyrics_page = requests.get(url, headers=header, proxies=Config.PROXY)
            soup = BeautifulSoup(lyrics_page.text, 'html.parser')
            props = extract_mxm_props(soup)
            if '"body":"' in props:
                lyrics = props.split('"body":"')[1].split('","language"')[0]
                lyrics = lyrics.replace("\\n", "\n")
                lyrics = lyrics.replace("\\", "")
                album = soup.find(class_="mxm-track-footer__album")
                if album:
                    song.album = album.find(class_="mui-cell__title").getText()
                if lyrics.strip():
                    return lyrics, lyrics_page.url, service_name


@lyrics_service
def _songmeanings(song):
    service_name = "Songmeanings"

    search_url = "http://songmeanings.com/m/query/?q=%s %s" % (song.artist, song.name)
    search_results = requests.get(search_url, proxies=Config.PROXY)
    soup = BeautifulSoup(search_results.text, 'html.parser')
    url = ""
    for link in soup.find_all('a', href=True):
        if "songmeanings.com/m/songs/view/" in link['href']:
            url = f"https:{link['href']}"
            break
        elif "/m/songs/view/" in link['href']:
            result = f"https://songmeanings.com{link['href']}"
            lyrics_page = requests.get(result, proxies=Config.PROXY)
            soup = BeautifulSoup(lyrics_page.text, 'html.parser')
            url = lyrics_page.url
            break
    lis = soup.find_all('ul', attrs={'data-inset': True})
    if len(lis) > 1:
        lyrics = lis[1].find_all('li')[1].getText()
        # lyrics = lyrics.encode('cp437', errors='replace').decode('utf-8', errors='replace')
        if "We are currently missing these lyrics." not in lyrics:
            return lyrics, url, service_name


@lyrics_service
def _songlyrics(song):
    service_name = "Songlyrics"
    artistm = song.artist.replace(" ", "-")
    songm = song.name.replace(" ", "-")
    url = f"https://www.songlyrics.com/{artistm}/{songm}-lyrics"
    lyrics_page = requests.get(url, proxies=Config.PROXY)
    soup = BeautifulSoup(lyrics_page.text, 'html.parser')
    lyrics_container = soup.find(id="songLyricsDiv")
    if lyrics_container:
        lyrics = lyrics_container.get_text()
        if "Sorry, we have no" not in lyrics and "We do not have" not in lyrics:
            title = soup.find("div", class_="pagetitle")
            if title:
                for info in title.find_all("p"):
                    if "Album:" in info.get_text():
                        song.album = info.find("a").get_text()
                        break
            return lyrics, lyrics_page.url, service_name


@lyrics_service
def _genius(song):
    service_name = "Genius"
    url = "https://genius.com/%s-%s-lyrics" % (song.artist.replace(' ', '-'), song.name.replace(' ', '-'))
    lyrics_page = requests.get(url, proxies=Config.PROXY)
    soup = BeautifulSoup(lyrics_page.text, 'html.parser')
    lyrics_container = soup.find("div", {"class": "lyrics"})
    if lyrics_container:
        lyrics = lyrics_container.get_text()
        if song.artist.lower().replace(" ", "") in soup.text.lower().replace(" ", ""):
            return lyrics, lyrics_page.url, service_name


@lyrics_service
def _versuri(song):
    service_name = "Versuri"
    search_url = "https://www.versuri.ro/q/%s+%s/" % \
                 (song.artist.replace(" ", "+").lower(), song.name.replace(" ", "+").lower())
    search_results = requests.get(search_url, proxies=Config.PROXY)
    soup = BeautifulSoup(search_results.text, 'html.parser')
    for search_results in soup.findAll('a'):
        if "/versuri/" in search_results['href']:
            link_text = search_results.getText().lower()
            if song.artist.lower() in link_text and song.name.lower() in link_text:
                url = "https://www.versuri.ro" + search_results['href']
                lyrics_page = requests.get(url, proxies=Config.PROXY)
                soup = BeautifulSoup(lyrics_page.text, 'html.parser')
                content = soup.find_all('div', {'id': 'pagecontent'})[0]
                lyrics = str(content)[str(content).find("</script><br/>") + 14:str(content).find("<br/><br/><center>")]
                lyrics = lyrics.replace("<br/>", "")
                if "nu există" not in lyrics:
                    return lyrics, lyrics_page.url, service_name


@lyrics_service
def _azapi(song):
    service = "Azapi"

    api = azapi.AZlyrics('duckduckgo', accuracy=0.5, proxies=Config.PROXY)

    if song.artist:
        api.artist = song.artist
        api.title = song.name

        try:
            songs = api.getSongs()
        except requests.exceptions.RequestException:
            api.search_engine = 'google'
            songs = api.getSongs()

        if song.name in songs:
            result_song = songs[song.name]

            song.album = result_song["album"]
            if result_song["year"]:
                song.year = int(result_song["year"])

            lyrics = api.getLyrics(url=result_song["url"])

            if isinstance(lyrics, str):
                return lyrics, result_song["url"], service


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
    except requests.exceptions.RequestException as error:
        print(f"cifraclub: {error}")
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
    url = 'https://www.songsterr.com/a/wa/bestMatchForQueryString?s={}&a={}'.format(title, artist)
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
    except requests.exceptions.RequestException as error:
        print("%s: %s" % ("Tanzmusik Online", error))
    except Exception as e:
        capture_exception(e)


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
    except requests.exceptions.RequestException as error:
        print("%s: %s" % ("Tanzschule Woelbing", error))
    except Exception as e:
        capture_exception(e)
