#!/usr/bin/env python3

# MIT License
#
# Copyright (c) 2022 Bryant Durrell
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import base64
import configparser
import csv
from datetime import datetime, date, timedelta
import feedparser
import re
import sqlite3
import sys
import time
import unicodedata

from bs4 import BeautifulSoup, Comment
import requests
import xxhash


# https://stackoverflow.com/a/34325723
def print_progress_bar(
    iteration,
    total,
    prefix="",
    suffix="",
    decimals=1,
    length=100,
    fill="█",
    printEnd="\r",
):
    """
    Call in a loop to create terminal progress bar
    @params:
        iteration   - Required  : current iteration (Int)
        total       - Required  : total iterations (Int)
        prefix      - Optional  : prefix string (Str)
        suffix      - Optional  : suffix string (Str)
        decimals    - Optional  : positive number of decimals in percent complete (Int)
        length      - Optional  : character length of bar (Int)
        fill        - Optional  : bar fill character (Str)
        printEnd    - Optional  : end character (e.g. "\r", "\r\n") (Str)
    """
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + "-" * (length - filledLength)
    print(f"\r{prefix} |{bar}| {percent}% {suffix}", end=printEnd)
    # Print New Line on Complete
    if iteration == total:
        print()


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

    review_html = clean_poster(review_html)

    if spoiler_flag:
        spoiler = review_html.find(
            name="em", string="This review may contain spoilers."
        )
        spoiler_p = spoiler.parent
        spoiler_p.extract()

    return review_html


def clean_poster(review_html):
    img_p = review_html.find("img", src=re.compile(r"\/film-poster\/|ltrbxd\.com"))
    if img_p:
        img_p.parent.extract()

    return review_html


def clean_review_title(title):
    return title.replace(" (contains spoilers)", "")


def spoiler_check(lb_url):
    review = requests.get(lb_url)
    html = BeautifulSoup(review.text, "html.parser")
    if html.find(
        "meta",
        content=(
            "This review may contain spoilers. "
            "Visit the page to bypass this warning and read the review."
        ),
    ):
        spoiler_flag = 1
    else:
        spoiler_flag = 0

    # Nap a little to avoid too much traffic to Letterboxd
    time.sleep(5)

    return spoiler_flag


def clean_database(config, dry_run):
    # Intended as an all purpose respository for database cleanup routines
    # Currently includes:
    #   - removing poster images

    if dry_run:
        dry_run_output = "DRY RUN: "
    else:
        dry_run_output = ""

    db_name = config["local"]["db_name"]
    try:
        db_conn = sqlite3.connect(db_name)
    except sqlite3.Error as error:
        print(f"Error connecting to db {db_name}: {error}")
        return

    db_conn.row_factory = sqlite3.Row
    db_cur = db_conn.cursor()

    db_cur.execute(
        "SELECT id, title, review FROM lb_feed WHERE review like '%img src%'",
    )
    movies = db_cur.fetchall()
    for movie in movies:
        print(f"{dry_run_output}Updating review for {movie['title']}")

        review_html = BeautifulSoup(movie["review"], "html.parser")
        review_html = clean_poster(review_html)

        if not dry_run:
            db_cur.execute(
                "UPDATE lb_feed SET review = ? WHERE id = ?",
                (str(review_html), movie["id"]),
            )

    db_conn.commit()
    db_cur.close()


def add_spoiler_field(csv_file_arg, dry_run):
    try:
        csv_file = open(csv_file_arg)
    except OSError as error:
        print(f"ERROR: {csv_file_arg} couldn't be opened for reading: {error}")
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
        print(f"DRY RUN: would write {len(all)} rows")
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

    if "wp/v2" not in response.json()["namespaces"]:
        print("ERROR: WP installation doesn't appear to support the v2 API")
        print("ERROR: Are you running version 4.7+?")

    return wp_api_url


def clean_wp_post_option(option_string):
    option_string.replace(" ", "")

    # Have a hairy regexp
    #    ^            anchors at the start of the string
    #    (\d+,{0,1})  any number of digits, optionally followed by a comma
    #    *            the previous group can appear zero or more times
    #                     this won't match an ID by itself, but the next bit will
    #    \d+          any number of digits
    #    $            anchors at the end of the string

    if re.search(r"^(\d+,{0,1})+\d+$", option_string):
        return option_string
    else:
        return False


def find_wp_post(config, post_title):
    post_id = 0
    page = 1

    wp_api_url = find_wp_api_url(config["wp"]["wp_url"])
    wp_search_api = f"{wp_api_url}wp/v2/search"

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
        print(f"DRY RUN: would write {movie['title']} to database.")
    else:
        print(f"Writing {movie['title']} to database.")
        pub_ts = datetime.fromtimestamp(time.mktime(movie["timestamp"]))
        try:
            sql = (
                "INSERT INTO lb_feed VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(title, year) DO NOTHING"
            )
            db_cur.execute(
                sql,
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
        except sqlite3.Error as error:
            print(f"ERROR: couldn't write {movie['title']}: {error}")
            return False


def write_movies_to_db(config, movies, dry_run):
    db_name = config["local"]["db_name"]
    try:
        db_conn = sqlite3.connect(db_name)
    except sqlite3.Error as error:
        print(f"Error connecting to db {db_name}: {error}")
        return

    db_cur = db_conn.cursor()

    for movie in movies:
        write_movie_to_db(db_cur, movie, dry_run)

    db_cur.close()


def fetch_lb_rss(user):
    reviews = []

    lb_feed = feedparser.parse(f"https://letterboxd.com/{user}/rss/")
    if lb_feed.bozo:
        print(f"Couldn't get/parse RSS feed for {user}: {lb_feed.bozo_exception}")
        return reviews

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

            # Handle null ratings
            if movie.letterboxd_memberrating:
                rating = movie.letterboxd_memberrating
            else:
                rating = None

            clean_review = clean_rss_review_html(movie.summary, spoiler_flag)

            reviews.append(
                {
                    "title": movie.letterboxd_filmtitle,
                    "link": movie.links[0]["href"],
                    "id": movie.id,
                    "timestamp": timestamp,
                    "review": str(clean_review),
                    "year": movie.letterboxd_filmyear,
                    "rating": rating,
                    "spoiler": spoiler_flag,
                }
            )

    return reviews


def fetch_lb_csv(csv_file_arg):
    reviews = []

    try:
        csv_file = open(csv_file_arg)
    except OSError as error:
        print(f"ERROR: {csv_file_arg} couldn't be opened for reading: {error}")
        return reviews

    reader = csv.DictReader(csv_file)
    # Building an array because we need total count for a progress bar
    movies = [line for line in reader]

    bar_prefix = "Movies:"
    bar_suffix = "Complete"
    bar_length = 50
    bar_total_count = len(movies)
    bar_current = 0

    print_progress_bar(
        bar_current,
        bar_total_count,
        prefix=bar_prefix,
        suffix=bar_suffix,
        length=bar_length,
    )

    for row in movies:
        # CSV exports are Unicode text w/embedded HTML tags
        # We first add <p> tags and clean up Unicode, then drop <br/> tags in
        # We add spoiler tags when writing to WP, since that's where they
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

        bar_current = bar_current + 1
        print_progress_bar(
            bar_current,
            bar_total_count,
            prefix=bar_prefix,
            suffix=bar_suffix,
            length=bar_length,
        )

    return reviews


def wp_post(config, post, dry_run, post_id=False):
    wp_api_url = find_wp_api_url(config["wp"]["wp_url"])
    wp_post_api = f"{wp_api_url}wp/v2/posts"
    wp_credentials = f'{config["wp"]["wp_user"]}:{config["wp"]["wp_key"]}'
    wp_token = base64.b64encode(wp_credentials.encode())
    wp_headers = {"Authorization": "Basic " + wp_token.decode("utf-8")}

    if dry_run:
        print("DRY RUN: writing or updating post to WordPress.")
    else:
        # I was gonna hash the content and compare before updating but WP returns
        # the content as rendered, which is different than the content as sent,
        # so screw it
        if post_id:
            try:
                response = requests.post(
                    f"{wp_post_api}/{post_id}", headers=wp_headers, json=post
                )
            except requests.exceptions as error:
                print(f"ERROR: post failed: {error}")
            if response.status_code != requests.codes.ok:
                print(f"ERROR: post failed w/status code {response.status_code}")
        else:
            try:
                response = requests.post(wp_post_api, headers=wp_headers, json=post)
            except requests.exceptions as error:
                print(f"ERROR: post failed: {error}")
            if response.status_code != requests.codes.ok:
                print(f"ERROR: post failed w/status code {response.status_code}")


def build_weekly_post(config, movie_list, week_start_datetime, week_end_datetime):
    if config["wp"]["cite"] == "cite":
        cite_start = "[cite]"
        cite_end = "[/cite]"
    else:
        cite_start = "<i>"
        cite_end = "</i>"

    date_fmt = "%-m/%-d/%Y"  # UNIX only, will fail under Windows

    title_list = []
    post_html = BeautifulSoup("", "html.parser")

    # Build the main body of the post
    for movie in movie_list:
        movie_title = movie[0]
        movie_year = int(movie[4])

        review_title = title_string(movie_title, movie_year, movie[5])
        title_list.append(f"{cite_start}{movie_title}{cite_end}")

        movie_review_html = BeautifulSoup(movie[3], "html.parser")

        # I could use foo.find() here instead of foo.find_all()[0]
        # but I felt like staying consistent with the end append
        if movie[6]:
            movie_review_html.find_all()[0].insert(0, "[spoiler]")
            movie_review_html.find_all()[-1].append("[/spoiler]")

        # Build the header elements -- the timestamps here are the date watched
        h2_html = movie_review_html.new_tag("h2")
        h2_html.string = (
            f"{movie[1].month}/{movie[1].day}/{movie[1].year}: {review_title}"
        )
        movie_review_html.find_all()[0].insert_before(h2_html)

        # Copy movie_review_html onto the end of post_html
        post_html.append(movie_review_html)

    # Build the rest of the post
    post_title = (
        f"Movie Reviews: {week_start_datetime.strftime(date_fmt)} "
        f"to {week_end_datetime.strftime(date_fmt)}"
    )

    # Add paragraphs for the <!-- more --> marker and title list
    # foo.find() provides the first tag in the document
    title_str = f"Movies reviewed this week: {oxfordcomma(title_list)}."
    title_list_p = post_html.new_tag("p")
    if config["wp"]["cite"] == "cite":
        title_list_p.string = title_str
    else:
        title_list_p.append(BeautifulSoup(title_str, "html.parser"))
    post_html.find().insert_before(title_list_p)

    more_p = post_html.new_tag("p")
    more_p.string = Comment("more")
    post_html.find("p").insert_after(more_p)

    # This could be more succinct but I would rather be clear

    if week_end_datetime > datetime.today():
        post_date = datetime.isoformat(datetime.today())
    else:
        post_date = datetime.isoformat(
            datetime(
                week_end_datetime.year, week_end_datetime.month, week_end_datetime.day
            )
        )

    post = {
        "title": post_title,
        "movies": title_list,  # For convenience
        "date": post_date,
        "content": str(post_html),
        "categories": config["wp"]["post_categories"],
        "tags": config["wp"]["post_tags"],
        "status": "publish",
    }

    return post


def write_movies_to_wp_by_week(config, dry_run, start_date, end_date):
    db_name = config["local"]["db_name"]

    wp_api_url = find_wp_api_url(config["wp"]["wp_url"])
    wp_search_api = f"{wp_api_url}wp/v2/search"

    # Convert date objects to beginning or end of day datetimes, as appropriate
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    week_start_datetime = start_datetime
    week_end_datetime = start_datetime + timedelta(days=6)

    movie_list = []

    try:
        db_conn = sqlite3.connect(
            db_name, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
    except sqlite3.Error as error:
        print(f"Error connecting to db {db_name}: {error}")
        return False

    db_cur = db_conn.cursor()

    while week_end_datetime <= end_datetime:
        sql = (
            "SELECT title, ts [timestamp], link, review, year, rating, spoilers "
            "FROM lb_feed WHERE ts >= ? AND ts <= ? ORDER BY ts ASC"
        )
        for movie in db_cur.execute(
            sql,
            (week_start_datetime, week_end_datetime),
        ):
            movie_list.append(movie)

        if movie_list:
            post = build_weekly_post(
                config, movie_list, week_start_datetime, week_end_datetime
            )

            search_payload = {"search": post["title"]}
            response = requests.get(wp_search_api, params=search_payload)

            if not response.json():
                if dry_run:
                    print(f"DRY RUN: not posting post {post['title']}")
                    print(f"DRY RUN: {', '.join(post['movies'])}")
                else:
                    print(f"Posting post {post['title']}")
                    wp_post(config, post, dry_run)
            else:
                if dry_run:
                    print(f"DRY RUN: not updating post {post['title']}")
                    print(f"DRY RUN: {', '.join(post['movies'])}")
                else:
                    print(f"Updating post {post['title']}")
                    wp_post(config, post, dry_run, post_id=response.json()[0]["id"])

        week_start_datetime = week_start_datetime + timedelta(days=7)
        week_end_datetime = week_end_datetime + timedelta(days=7)
        movie_list = []

    return True


def write_movies_to_wp(config, dry_run, start_date, end_date):
    db_name = config["local"]["db_name"]

    try:
        db_conn = sqlite3.connect(
            db_name, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
    except sqlite3.Error as error:
        print(f"Error connecting to db {db_name}: {error}")
        return

    # Convert date objects to beginning or end of day datetimes, as appropriate
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())

    db_cur = db_conn.cursor()

    sql = (
        "SELECT title, ts [timestamp], link, review, year, rating, spoilers "
        "FROM lb_feed "
        "WHERE ts >= ? AND ts <= ? ORDER BY ts ASC"
    )
    for movie in db_cur.execute(
        sql,
        ([start_datetime, end_datetime]),
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
            "categories": config["wp"]["post_categories"],
            "tags": config["wp"]["post_tags"],
            "status": "publish",
        }

        if dry_run:
            print(f"DRY RUN: not posting {movie[0]}")
            print(str(post_html))
        else:
            print(f"Posting {movie[0]}")
            wp_post(config, post, dry_run, post_id=existing_post_id)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "action",
        help="Action for the script to take",
        choices=[
            "fetchrss",
            "fetchcsv",
            "write",
            "writeweeks",
            "cleandb",
            "addspoilers",
        ],
    )

    parser.add_argument(
        "-c",
        "--config",
        action="store",
        default="lb_feed.conf",
        help="Configuration files (defaults to lb_feed.conf)",
    )
    parser.add_argument(
        "--csv",
        action="store",
        default="reviews.csv",
        help="Letterboxd export file to read from (defaults to reviews.csv)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Don't write to WordPress or SQLite DB",
    )
    parser.add_argument(
        "--start-date",
        action="store",
        type=date.fromisoformat,
        default="1970-01-05",
        help="Start date for posts in YYYY-MM-DD format (defaults to 1970-01-05)",
    )
    parser.add_argument(
        "--end-date",
        action="store",
        type=date.fromisoformat,
        default=date.today(),
        help="End date for posts in YYYY-MM-DD format (defaults to today)",
    )

    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.config)

    # Check for necessary config options
    option_missing = False
    for wp_option in ["wp_key", "wp_url", "wp_user"]:
        if not config.has_option("wp", wp_option):
            option_missing = True
            print(f"ERROR: wp/{wp_option} missing from {args.config}")

    for lb_option in ["lb_user"]:
        if not config.has_option("lb", lb_option):
            option_missing = True
            print(f"ERROR: lb/{lb_option} missing from {args.config}")

    if option_missing:
        sys.exit()

    # Check for config options that can be absent; maybe make this fallbacks later
    if not config.has_option("local", "db_name"):
        config["local"]["db_name"] = "lb_feed.sqlite"
    if not config.has_option("wp", "cite"):
        config["wp"]["cite"] = "italic"

    for wp_post_option in ["post_categories", "post_tags"]:
        if config.has_option("wp", wp_post_option):
            clean_option_string = clean_wp_post_option(config["wp"][wp_post_option])
            if clean_option_string:
                config["wp"][wp_post_option] = clean_option_string
            else:
                print(
                    f"ERROR: {wp_post_option} should be a comma separated list of "
                    f"digits, but is \"{config['wp'][wp_post_option]}\""
                )
                sys.exit()
        else:
            config["wp"][wp_post_option] = ""

    if args.action == "fetchrss":
        reviews = fetch_lb_rss(config["lb"]["lb_user"])
        write_movies_to_db(config, reviews, args.dry_run)
    elif args.action == "fetchcsv":
        reviews = fetch_lb_csv(args.csv)
        write_movies_to_db(config, reviews, args.dry_run)
    elif args.action == "write":
        write_movies_to_wp(config, args.dry_run, args.start_date, args.end_date)
    elif args.action == "writeweeks":
        # Move the --start-date and --end-date parameters to full weeks
        if args.start_date.isoweekday() != 1:
            args.start_date = args.start_date - timedelta(
                days=args.start_date.isoweekday() - 1
            )
            print(f"Adjusting --start-date to a Monday ({args.start_date})")
        if args.end_date.isoweekday() != 7:
            args.end_date = args.end_date + timedelta(
                days=7 - args.end_date.isoweekday()
            )
            print(f"Adjusting --end-date to a Sunday ({args.end_date})")
        write_movies_to_wp_by_week(config, args.dry_run, args.start_date, args.end_date)
    elif args.action == "cleandb":
        clean_database(config, args.dry_run)
    elif args.action == "addspoilers":
        add_spoiler_field(args.csv, args.dry_run)


if __name__ == "__main__":
    main()
