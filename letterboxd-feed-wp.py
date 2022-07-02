#!/usr/bin/env python3

import argparse
import base64
import configparser
import csv
from datetime import datetime, date
import feedparser
import re
import sqlite3
import sys
import time
import unicodedata

from bs4 import BeautifulSoup, Comment
import requests
import xxhash


def oxfordcomma(titles):
    if len(titles) == 0:
        return ""
    if len(titles) == 1:
        return titles[0]
    if len(titles) == 2:
        return titles[0] + " and " + titles[1]
    return ", ".join(titles[:-1]) + ", and " + titles[-1]


def title_string(movie_title, movie_year, movie_rating):
    parsed_title = f"{movie_title} ({movie_year})"
    if movie_rating:
        parsed_title = f"{parsed_title}: {'*' * int(movie_rating)}"
        if movie_rating % 1:
            parsed_title = f"{parsed_title}1/2"
    return parsed_title


def clean_rss_review_html(review, spoiler_flag):
    # This function works for reviews retrieved via RSS; don't go making the same
    # mistake I already made once and adding any other functionality to it

    clean_review = unicodedata.normalize("NFKD", review)
    review_html = BeautifulSoup(clean_review, "html.parser")

    img_p = review_html.find("img", src=re.compile("\/film-poster\/"))
    if img_p:
        img_p.parent.extract()

    if spoiler_flag:
        spoiler = review_html.find(
            name="em", string="This review may contain spoilers."
        )
        spoiler_p = spoiler.parent
        spoiler_p.extract()

    return review_html


def clean_review_title(title):
    return title.replace(" (contains spoilers)", "")


def spoiler_check(lb_url):
    review = requests.get(lb_url)
    html = BeautifulSoup(review.text, "html.parser")
    if html.find(
        "meta",
        content="This review may contain spoilers. Visit the page to bypass this warning and read the review.",
    ):
        spoiler_flag = 1
    else:
        spoiler_flag = 0

    # Nap a little to avoid too much traffic to Letterboxd
    time.sleep(5)

    return spoiler_flag


def add_spoiler_field(csv_file_arg, dry_run):
    try:
        csv_file = open(csv_file_arg)
    except:
        print(f"{csv_file_arg} not found")
        return

    reader = csv.reader(csv_file)
    writer = csv.writer(sys.stdout, dialect="unix")

    all = []
    row = next(reader)
    row.append("Spoilers")

    for row in reader:
        spoiler_flag = spoiler_check(row[3])
        row.append(spoiler_flag)
        all.append(row)

    if dry_run:
        print(f"Dry run: would write {len(all)} rows")
    else:
        writer.writerows(all)


def find_wp_api_url(wp_base_url):
    try:
        response = requests.head(wp_base_url)
    except requests.RequestException as e:
        print(f"ERROR: couldn't get base WP URL {wp_base_url}: {e}")
        return False

    if "Link" not in response.headers:
        print(f"ERROR: couldn't find WP API link at {wp_base_url}")
        return False

    wp_api_url = response.links["https://api.w.org/"]["url"]

    try:
        response = requests.get(wp_api_url)
    except requests.RequestException as e:
        print(f"ERROR: couldn't get WP API URIL{wp_api_url}: {e}")
        return False

    if not "wp/v2" in response.json()["namespaces"]:
        print(f"ERROR: WP installation doesn't appear to support the v2 API")
        print(f"ERROR: Are you running version 4.7+?")

    return wp_api_url


def find_wp_post(config, post_title):
    post_id = 0
    page = 1

    wp_api_url = find_wp_api_url(config["wp"]["wp_url"])
    wp_search_api = f"{wp_api_url}wp/v2/search"
    wp_credentials = f'{config["wp"]["wp_user"]}:{config["wp"]["wp_key"]}'
    wp_token = base64.b64encode(wp_credentials.encode())
    wp_headers = {"Authorization": "Basic " + wp_token.decode("utf-8")}

    print(f"searching for {post_title}")
    search_payload = {
        "search": post_title,
        "_fields": "title,id",
        "page": page,
    }

    while search_payload["page"]:
        response = requests.get(wp_search_api, params=search_payload)
        for result in response.json():
            if result["title"] == post_title:
                post_id = result["id"]
                print(f"found {post_id}")

        if "next" in response.links:
            search_payload["page"] = search_payload["page"] + 1
        else:
            search_payload["page"] = 0

    return post_id


def write_movie_to_db(db_cur, movie, dry_run):
    if dry_run:
        print(f"Dry run: would write {movie['title']} to database.")
    else:
        print(f"Writing {movie['title']} to database.")
        pub_ts = datetime.fromtimestamp(time.mktime(movie["timestamp"]))
        try:
            db_cur.execute(
                "INSERT INTO lb_feed VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(title, year) DO NOTHING",
                (
                    movie["id"],
                    movie["title"],
                    pub_ts,
                    movie["link"],
                    movie["review"],
                    movie["year"],
                    movie["rating"],
                    movie["spoiler"],
                ),
            )
            db_cur.connection.commit()
            return True
        except sqlite3.Error as e:
            print(f"ERROR: couldn't write {movie['title']}: {e}")
            return False


def write_movies_to_db(config, movies, dry_run):
    db_name = config["local"]["db_name"]
    try:
        db_conn = sqlite3.connect(db_name)
    except:
        print("Error connecting to db {db_name}")
        return

    db_cur = db_conn.cursor()

    for movie in movies:
        write_movie_to_db(db_cur, movie, dry_run)

    db_cur.close()


def fetch_lb_rss(user):
    try:
        lb_feed = feedparser.parse(f"https://letterboxd.com/{user}/rss/")
    except:
        print("Couldn't get/parse RSS feed for {user}")
        return

    reviews = []
    for movie in lb_feed.entries:
        if "letterboxd-review-" in movie["guid"]:
            # Weak parse for spoilers but this is as good as it gets
            if "(contains spoilers)" in movie.title:
                spoiler_flag = 1
            else:
                spoiler_flag = 0

            # Either the date we watched the movie, or the date the review was published
            if movie.letterboxd_watcheddate:
                timestamp = time.strptime(movie.letterboxd_watcheddate, "%Y-%m-%d")
            else:
                timestamp = movie.published_parsed

            clean_review = clean_rss_review_html(movie.summary, spoiler_flag)

            reviews.append(
                {
                    "title": movie.letterboxd_filmtitle,
                    "link": movie.links[0]["href"],
                    "id": movie.id,
                    "timestamp": movie.published_parsed,
                    "review": str(clean_review),
                    "year": movie.letterboxd_filmyear,
                    "rating": movie.letterboxd_memberrating,
                    "spoiler": spoiler_flag,
                }
            )

    return reviews


def fetch_lb_csv(csv_file_arg):
    try:
        csv_file = open(csv_file_arg)
    except:
        print(f"{csv_file_arg} not found")
        return

    reviews = []
    reader = csv.DictReader(csv_file)
    for row in reader:
        # CSV exports are Unicode text w/embedded HTML tags
        # We first add <p> tags and clean up Unicode, then drop <br/> tags in
        # Plan is to add spoiler tags when writing to WP, since that's where they
        # make sense in context
        parsed_review = "".join(
            map(
                lambda x: f"<p>{x}</p>",
                filter(
                    None, unicodedata.normalize("NFKD", row["Review"]).split("\n\n")
                ),
            )
        )
        parsed_review = parsed_review.replace("\n", "<br />")

        # Generate a unique ID using a hash on the movie + year
        generated_id = "letterboxd-review-" + str(
            xxhash.xxh64(f"{row['Name']}{row['Year']}").intdigest()
        )

        # Pick a date for when it was watched (could be the date the review was added)
        if row["Watched Date"]:
            parsed_date = date.fromisoformat(row["Watched Date"]).timetuple()
        else:
            parsed_date = date.fromisoformat(row["Date"]).timetuple()

        # Someday I'm gonna get confused and run this on a CSV file with spoilers in
        # it already, so let's just catch both cases
        if "Spoilers" in row:
            spoiler_flag = row["Spoilers"]
        else:
            spoiler_flag = spoiler_check(row["Letterboxd URI"])

        reviews.append(
            {
                "title": row["Name"],
                "link": row["Letterboxd URI"],
                "id": generated_id,
                "timestamp": parsed_date,
                "review": parsed_review,
                "year": row["Year"],
                "rating": row["Rating"],
                "spoiler": spoiler_flag,
            }
        )

    return reviews


def wp_post(config, post, dry_run, post_id=False):
    wp_api_url = find_wp_api_url(config["wp"]["wp_url"])
    wp_post_api = f"{wp_api_url}wp/v2/posts"
    wp_credentials = f'{config["wp"]["wp_user"]}:{config["wp"]["wp_key"]}'
    wp_token = base64.b64encode(wp_credentials.encode())
    wp_headers = {"Authorization": "Basic " + wp_token.decode("utf-8")}

    if dry_run:
        print(f"Dry run: writing or updating post to WordPress.")
    else:
        if post_id:
            response = requests.post(
                f"{wp_post_api}/{post_id}", headers=wp_headers, json=post
            )
            # I was gonna hash the content and compare before updating but WP returns the
            # content as rendered, which is different than the content as sent, so screw it

        else:
            response = requests.post(wp_post_api, headers=wp_headers, json=post)


def write_movies_to_wp_by_week(config, dry_run, start_date, end_date):
    db_name = config["local"]["db_name"]

    wp_api_url = find_wp_api_url(config["wp"]["wp_url"])
    wp_search_api = f"{wp_api_url}wp/v2/search"
    wp_credentials = f'{config["wp"]["wp_user"]}:{config["wp"]["wp_key"]}'
    wp_token = base64.b64encode(wp_credentials.encode())
    wp_headers = {"Authorization": "Basic " + wp_token.decode("utf-8")}

    post_categories = config["wp"]["post_categories"]
    post_tags = config["wp"]["post_tags"]

    movie_list = {}
    date_fmt = "%-m/%-d/%Y"  # UNIX only, will fail under Windows

    try:
        db_conn = sqlite3.connect(
            db_name, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
    except:
        print("Error connecting to db {db_name}")
        return False

    db_cur = db_conn.cursor()

    for row in db_cur.execute(
        "SELECT title, ts [timestamp], link, review, year, rating, spoilers FROM lb_feed ORDER BY ts ASC"
    ):
        year = row[1].year
        week = row[1].isocalendar().week
        if year not in movie_list:
            movie_list[year] = {}
        if week not in movie_list[year]:
            movie_list[year][week] = []
        movie_list[year][week].append(row)

    for year in movie_list:
        for week in movie_list[year]:
            # Title & date material
            title_list = []
            start_date = date.fromisocalendar(year, week, 1)
            end_date = date.fromisocalendar(year, week, 7)
            post_title = f"Movie Reviews: {start_date.strftime(date_fmt)} to {end_date.strftime(date_fmt)}"

            # Build the post movie by movie
            post_html = BeautifulSoup("", "html.parser")

            for movie in movie_list[year][week]:
                movie_title = movie[0]
                movie_year = int(movie[4])

                review_title = title_string(movie_title, movie_year, movie[5])
                title_list.append(f"[cite]{movie_title}[/cite]")

                movie_review_html = BeautifulSoup(movie[3], "html.parser")

                # Note: I could use foo.find() here instead of foo.find_all()[0]
                # but I felt like staying consistent with the end append
                if movie[6]:
                    movie_review_html.find_all()[0].insert(0, "[spoiler]")
                    movie_review_html.find_all()[-1].append("[/spoiler]")

                # Build the header elements -- the timestamps here are date watched
                h2_html = movie_review_html.new_tag("h2")
                h2_html.string = (
                    f"{movie[1].month}/{movie[1].day}/{movie[1].year}: {review_title}"
                )
                movie_review_html.find_all()[0].insert_before(h2_html)

                # Copy movie_review_html onto the end of post_html
                post_html.append(movie_review_html)

            # Add paragraphs for the <!-- more --> marker and title list
            # foo.find() provides the first tag in the document

            title_list_p = post_html.new_tag("p")
            title_list_p.string = (
                f"Movies reviewed this week: {oxfordcomma(title_list)}."
            )
            post_html.find().insert_before(title_list_p)

            more_p = post_html.new_tag("p")
            more_p.string = Comment("more")
            post_html.find("p").insert_after(more_p)

            post_date = datetime.isoformat(
                datetime(end_date.year, end_date.month, end_date.day)
            )
            post = {
                "title": post_title,
                "date": post_date,
                "content": str(post_html),
                "categories": post_categories,
                "tags": post_tags,
                "status": "publish",
            }

            search_payload = {"search": post_title}
            response = requests.get(wp_search_api, params=search_payload)
            if not response.json():
                if dry_run:
                    print(f"Dry run: not posting {post_title}")
                else:
                    print(f"posting {post_title}")
                    wp_post(config, post, dry_run)
            else:
                if dry_run:
                    print(f"Dry run: not updating {post_title}")
                else:
                    print(f"updating {post_title}")
                    # For fuck's sake clean this up
                    post_response = requests.get(
                        f"{config['wp']['wp_url']}/wp-json/wp/v2/posts/{response.json()[0]['id']}"
                    )
                    wp_post(config, post, dry_run, post_id=post_response.json()["id"])

    return True


def write_movies_to_wp(config, dry_run, start_date, end_date):
    db_name = config["local"]["db_name"]

    wp_api_url = find_wp_api_url(config["wp"]["wp_url"])
    wp_search_api = f"{wp_api_url}wp/v2/search"
    wp_credentials = f'{config["wp"]["wp_user"]}:{config["wp"]["wp_key"]}'
    wp_token = base64.b64encode(wp_credentials.encode())
    wp_headers = {"Authorization": "Basic " + wp_token.decode("utf-8")}

    post_categories = config["wp"]["post_categories"]

    try:
        db_conn = sqlite3.connect(
            db_name, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
    except:
        print("Error connecting to db {db_name}")
        return

    db_cur = db_conn.cursor()

    for movie in db_cur.execute(
        "SELECT title, ts [timestamp], link, review, year, rating, spoilers FROM lb_feed"
    ):
        post_title = title_string(movie[0], movie[4], movie[5])
        print(post_title)
        existing_post_id = find_wp_post(config, post_title)

        post_html = BeautifulSoup(movie[3], "html.parser")
        post_date = datetime.isoformat(movie[1])

        if movie[6]:
            more_p = post_html.new_tag("p")
            more_p.string = Comment("more")
            post_html.find().insert_before(more_p)

            spoilers_p = post_html.new_tag("p")
            spoilers_p.string = "This review contains spoilers."
            post_html.find().insert_before(spoilers_p)

        post = {
            "title": post_title,
            "date": post_date,
            "content": str(post_html),
            "categories": post_categories,
            "status": "publish",
        }

        if dry_run:
            print(f"Dry run: not posting {movie[0]}")
            print(str(post_html))
        else:
            print(f"posting {movie[0]}")
            wp_post(config, post, dry_run, post_id=existing_post_id)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "action",
        help="Action for the script to take",
        choices=["fetchrss", "fetchcsv", "write", "writeweeks", "addspoilers"],
    )

    parser.add_argument("-c", "--config", action="store", default="lb_feed.conf", help="Configuration files (defaults to lb_feed.conf)")
    parser.add_argument("--csv", action="store", default="reviews.csv", help="Letterboxd export file to read from (defaults to reviews.csv)")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Don't write to WordPress or SQLite DB")
    parser.add_argument(
        "--start-date",
        action="store",
        type=date.fromisoformat,
        default="1970-01-01",
        help="Start date in YYYY-MM-DD format (defaults to 1970-01-01)",
    )
    parser.add_argument(
        "--end-date",
        action="store",
        type=date.fromisoformat,
        default=date.today(),
        help="Start date in YYYY-MM-DD format (defaults to today)",
    )

    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.config)

    # Check for all the config options
    option_missing = False
    for wp_option in ["wp_key", "wp_url", "wp_user"]:
        if not config.has_option("wp", wp_option):
            option_missing = True
            print(f"ERROR: wp/{wp_option} missing from {args.config}")

    for lb_option in ["lb_user"]:
        if not config.has_option("lb", lb_option):
            option_missing = True
            print(f"ERROR: lb/{lb_option} missing from {args.config}")

    if not config.has_option("local", "db_name"):
        config["local"]["db_name"] = "lb_feed.sqlite"

    if option_missing:
        sys.exit()

    if args.action == "fetchrss":
        reviews = fetch_lb_rss(config["lb"]["lb_user"])
        write_movies_to_db(config, reviews, args.dry_run)
    elif args.action == "fetchcsv":
        reviews = fetch_lb_csv(args.csv)
        write_movies_to_db(config, reviews, args.dry_run)
    elif args.action == "write":
        write_movies_to_wp(config, args.dry_run, args.start_date, args.end_date)
    elif args.action == "writeweeks":
        write_movies_to_wp_by_week(config, args.dry_run, args.start_date, args.end_date)
    elif args.action == "addspoilers":
        add_spoiler_field(args.csv, args.dry_run)


if __name__ == "__main__":
    main()
