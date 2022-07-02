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

    payload = {"page": 1}
    
    while payload["page"]:
        try:
            response = requests.get(f"{wp_api_url}wp/v2/{args.action}", payload)
        except requests.RequestException as e:
            print(f"ERROR: couldn't get {args.action} values: {e}")
            return False

        for item in response.json():
            print(f"{item['id']}: {item['name']}")
            
        if "next" in response.links:
            payload["page"] = payload["page"] + 1
        else:
            payload["page"] = 0

if __name__ == "__main__":
    main()
