#!/usr/bin/env python3

import argparse
import configparser
import requests


def find_wp_api_url(wp_base_url):
    try:
        response = requests.head(wp_base_url)
    except requests.RequestException as e:
        print(f"ERROR: couldn't get base WP URL {wp_base_url}: {e}")
        return False

    if "Link" not in response.headers:
        print(f"ERROR: couldn't find WP API link at {wp_base_url}")
        return False

    # Probably a little cleaner to do this with a regex but this works  
    wp_api_url = response.headers["Link"].partition(">;")[0][1:]
    
    try:
        response = requests.get(wp_api_url)
    except requests.RequestException as e:
        print(f"ERROR: couldn't get WP API URIL{wp_api_url}: {e}")
        return False

    if not "wp/v2" in response.json()["namespaces"]:
        print(f"ERROR: WP installation doesn't appear to support the v2 API")
        print(f"ERROR: Are you running version 4.7+?")

    return wp_api_url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        help="Specify values to retrieve",
        choices=["categories", "tags"],
    )
    parser.add_argument("-c", "--config", action="store", default="lb_feed.conf")
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.config)

    # Check for important config option
    if not config.has_option("wp", "wp_url"):
        print(f"ERROR: wp/wp_url missing from {args.config}")
        sys.exit()

    wp_api_url = find_wp_api_url(config["wp"]["wp_url"])
    
    try:
        response = requests.get(f"{wp_api_url}wp/v2/{args.action}")
    except requests.RequestException as e:
        print(f"ERROR: couldn't get {args.action} values: {e}")
        return False

    for item in response.json():
        print(f"{item['id']}: {item['name']}")

if __name__ == "__main__":
    main()
