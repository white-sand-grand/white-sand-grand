#!/usr/bin/env python3

import argparse
from datetime import datetime, timedelta, timezone
from html import escape
import json
import os
from pathlib import Path
import sys
from typing import Any, Optional, Union
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
AFFILIATED_REPOSITORIES_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    repositories(
      first: 100
      after: $after
      ownerAffiliations: [OWNER, ORGANIZATION_MEMBER, COLLABORATOR]
      orderBy: {direction: DESC, field: STARGAZERS}
    ) {
      nodes {
        forkCount
        isFork
        nameWithOwner
        stargazerCount
      }
      pageInfo {
        endCursor
        hasNextPage
      }
    }
  }
}
"""

LANGUAGE_COLORS = {
    "C": "#555555",
    "C++": "#f34b7d",
    "CSS": "#663399",
    "Go": "#00add8",
    "HTML": "#e34c26",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "Jupyter Notebook": "#da5b0b",
    "Kotlin": "#a97bff",
    "Python": "#3572a5",
    "Rust": "#dea584",
    "Shell": "#89e051",
    "Swift": "#f05138",
    "TypeScript": "#3178c6",
}

FALLBACK_LANGUAGE_COLORS = (
    "#0969da",
    "#8250df",
    "#bf8700",
    "#1a7f37",
    "#cf222e",
    "#0550ae",
)

THEMES = {
    "light": {
        "background": "#ffffff",
        "border": "#d0d7de",
        "accent": "#0969da",
        "label": "#57606a",
        "value": "#24292f",
        "divider": "#d8dee4",
        "footer": "#6e7781",
        "rank_track": "#d8dee4",
    },
    "dark": {
        "background": "#0d1117",
        "border": "#30363d",
        "accent": "#58a6ff",
        "label": "#8b949e",
        "value": "#c9d1d9",
        "divider": "#21262d",
        "footer": "#6e7681",
        "rank_track": "#30363d",
    },
}


class GitHubAPIError(RuntimeError):
    pass


def request_json(
    url: str,
    token: str,
    payload: Optional[dict[str, Any]] = None,
) -> Union[dict[str, Any], list[dict[str, Any]]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "white-sand-grand-profile-stats",
        "X-GitHub-Api-Version": API_VERSION,
    }
    if data is not None:
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            response = json.loads(error.read().decode("utf-8"))
            message = response.get("message", str(error))
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = str(error)
        raise GitHubAPIError(
            f"GitHub API request failed with HTTP {error.code}: {message}"
        ) from None
    except URLError as error:
        raise GitHubAPIError(f"GitHub API request failed: {error.reason}") from None


def fetch_repositories(
    username: str, token: str, repository_type: str = "owner"
) -> list[dict[str, Any]]:
    repositories: list[dict[str, Any]] = []
    page = 1

    while True:
        query = urlencode(
            {
                "type": repository_type,
                "sort": "full_name",
                "per_page": 100,
                "page": page,
            }
        )
        response = request_json(
            f"{API_ROOT}/users/{quote(username)}/repos?{query}", token
        )
        if not isinstance(response, list):
            raise GitHubAPIError("GitHub repositories response was not a list")

        repositories.extend(response)
        if len(response) < 100:
            return repositories
        page += 1


def fetch_affiliated_repositories(
    username: str, token: str
) -> Optional[list[dict[str, Any]]]:
    repositories: list[dict[str, Any]] = []
    cursor = None

    for _ in range(10):
        response = request_json(
            f"{API_ROOT}/graphql",
            token,
            {
                "query": AFFILIATED_REPOSITORIES_QUERY,
                "variables": {"login": username, "after": cursor},
            },
        )
        if not isinstance(response, dict):
            raise GitHubAPIError("GitHub affiliated repositories response was invalid")

        if response.get("errors"):
            print(
                "::warning::Affiliated repository statistics were unavailable; "
                "using owned public repositories for rank calculation."
            )
            return None

        try:
            connection = response["data"]["user"]["repositories"]
            for repository in connection["nodes"]:
                repositories.append(
                    {
                        "fork": repository["isFork"],
                        "forks_count": repository["forkCount"],
                        "full_name": repository["nameWithOwner"],
                        "stargazers_count": repository["stargazerCount"],
                    }
                )
            if not connection["pageInfo"]["hasNextPage"]:
                return repositories or None
            cursor = connection["pageInfo"]["endCursor"]
        except (KeyError, TypeError):
            raise GitHubAPIError(
                "GitHub affiliated repositories response was invalid"
            ) from None

    print(
        "::warning::Affiliated repository statistics exceeded 1,000 repositories; "
        "rank uses the first 1,000 ordered by stars."
    )
    return repositories


def language_color(name: str, api_color: Optional[str] = None) -> str:
    if api_color:
        return api_color
    if name in LANGUAGE_COLORS:
        return LANGUAGE_COLORS[name]
    color_index = sum(
        (index + 1) * ord(character) for index, character in enumerate(name)
    ) % len(FALLBACK_LANGUAGE_COLORS)
    return FALLBACK_LANGUAGE_COLORS[color_index]


def fetch_language_repositories(
    repositories: list[dict[str, Any]], token: str
) -> list[dict[str, Any]]:
    language_repositories: list[dict[str, Any]] = []

    for repository in repositories:
        if repository.get("fork"):
            continue
        full_name = repository.get("full_name")
        if not isinstance(full_name, str):
            continue
        try:
            response = request_json(
                f"{API_ROOT}/repos/{quote(full_name, safe='/')}/languages", token
            )
        except GitHubAPIError as error:
            print(f"::warning::{full_name} language data was unavailable: {error}")
            response = {}
        if not isinstance(response, dict):
            response = {}

        primary_name = repository.get("language")
        primary_language = (
            {
                "color": language_color(primary_name),
                "name": primary_name,
            }
            if isinstance(primary_name, str)
            else None
        )
        language_repositories.append(
            {
                "full_name": full_name,
                "primary_language": primary_language,
                "languages": [
                    {
                        "color": language_color(name),
                        "name": name,
                        "size": size,
                    }
                    for name, size in response.items()
                    if isinstance(name, str) and isinstance(size, int)
                ],
            }
        )

    return language_repositories


def fetch_contributed_repository_names(username: str, token: str) -> set[str]:
    since = (datetime.now(timezone.utc) - timedelta(days=365)).date().isoformat()
    searches = (
        ("commits", f"author:{username} author-date:>={since}", 3),
        ("issues", f"author:{username} type:pr is:public", 2),
        ("issues", f"reviewed-by:{username} type:pr is:public", 2),
    )
    repository_names: set[str] = set()

    for endpoint, search_query, page_limit in searches:
        for page in range(1, page_limit + 1):
            query = urlencode(
                {
                    "q": search_query,
                    "per_page": 100,
                    "page": page,
                }
            )
            try:
                response = request_json(
                    f"{API_ROOT}/search/{endpoint}?{query}", token
                )
            except GitHubAPIError as error:
                print(
                    "::warning::Contributed repository discovery was incomplete: "
                    f"{error}"
                )
                break
            if not isinstance(response, dict) or not isinstance(
                response.get("items"), list
            ):
                print("::warning::GitHub contribution search response was invalid")
                break

            items = response["items"]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if endpoint == "commits":
                    repository = item.get("repository")
                    full_name = (
                        repository.get("full_name")
                        if isinstance(repository, dict)
                        else None
                    )
                else:
                    repository_url = item.get("repository_url")
                    full_name = (
                        "/".join(repository_url.rstrip("/").split("/")[-2:])
                        if isinstance(repository_url, str)
                        else None
                    )
                if isinstance(full_name, str) and "/" in full_name:
                    repository_names.add(full_name)

            if len(items) < 100:
                break

    return repository_names


def add_contributed_public_repositories(
    repositories: list[dict[str, Any]], username: str, token: str
) -> list[dict[str, Any]]:
    repositories_by_name = {
        repository["full_name"]: repository
        for repository in repositories
        if isinstance(repository.get("full_name"), str)
    }

    for full_name in sorted(fetch_contributed_repository_names(username, token)):
        if full_name in repositories_by_name:
            continue
        try:
            repository = request_json(
                f"{API_ROOT}/repos/{quote(full_name, safe='/')}", token
            )
        except GitHubAPIError as error:
            print(f"::warning::{full_name} metadata was unavailable: {error}")
            continue
        if (
            isinstance(repository, dict)
            and not repository.get("private")
            and isinstance(repository.get("full_name"), str)
        ):
            repositories_by_name[repository["full_name"]] = repository

    return list(repositories_by_name.values())


def fetch_search_count(search_query: str, endpoint: str, token: str) -> int:
    query = urlencode({"q": search_query, "per_page": 1})
    response = request_json(f"{API_ROOT}/search/{endpoint}?{query}", token)
    if not isinstance(response, dict) or not isinstance(response.get("total_count"), int):
        raise GitHubAPIError(f"GitHub {endpoint} search response was invalid")
    return response["total_count"]


def format_value(value: int) -> str:
    return f"{value:,}"


def calculate_rank(
    commits: int,
    pull_requests: int,
    issues: int,
    reviews: int,
    stars: int,
    followers: int,
) -> tuple[str, float]:
    def exponential_cdf(value: float) -> float:
        return 1 - 2 ** -value

    def log_normal_cdf(value: float) -> float:
        return value / (1 + value)

    weighted_score = (
        2 * exponential_cdf(commits / 250)
        + 3 * exponential_cdf(pull_requests / 50)
        + exponential_cdf(issues / 25)
        + exponential_cdf(reviews / 2)
        + 4 * log_normal_cdf(stars / 50)
        + log_normal_cdf(followers / 10)
    )
    percentile = max(0.0, min(100.0, (1 - weighted_score / 12) * 100))
    thresholds = (1, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100)
    levels = ("S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C")
    level = next(
        level
        for threshold, level in zip(thresholds, levels)
        if percentile <= threshold
    )
    return level, percentile


def render_card(
    username: str,
    profile: dict[str, Any],
    repositories: list[dict[str, Any]],
    commits: int,
    pull_requests: int,
    issues: int,
    reviews: int,
    theme_name: str,
) -> str:
    theme = THEMES[theme_name]
    original_repositories = [repo for repo in repositories if not repo.get("fork")]
    stars = sum(int(repo.get("stargazers_count", 0)) for repo in original_repositories)
    forks = sum(int(repo.get("forks_count", 0)) for repo in original_repositories)
    rank, percentile = calculate_rank(
        commits,
        pull_requests,
        issues,
        reviews,
        stars,
        int(profile.get("followers", 0)),
    )

    left_stats = (
        ("Public repositories", int(profile.get("public_repos", len(repositories)))),
        ("Stars earned", stars),
        ("Repository forks", forks),
        ("Followers", int(profile.get("followers", 0))),
    )
    right_stats = (
        ("Commits authored (last year)", commits),
        ("Pull requests authored", pull_requests),
        ("Issues authored", issues),
        ("Pull requests reviewed", reviews),
    )

    def render_column(stats: tuple[tuple[str, int], ...], x: int) -> str:
        rows = []
        for index, (label, value) in enumerate(stats):
            y = 88 + index * 34
            rows.append(
                f'<circle cx="{x}" cy="{y - 5}" r="3" fill="{theme["accent"]}"/>'
                f'<text x="{x + 14}" y="{y}" class="label">{escape(label)}</text>'
                f'<text x="{x + 275}" y="{y}" class="value" text-anchor="end">'
                f"{format_value(value)}</text>"
            )
        return "".join(rows)

    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    safe_username = escape(username)
    title = f"{safe_username}'s GitHub Stats"
    rank_circumference = 276.46
    rank_offset = rank_circumference * percentile / 100

    return f'''<svg width="760" height="235" viewBox="0 0 760 235" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc">
  <title id="title">{title}</title>
  <desc id="desc">A daily static snapshot of verified GitHub profile statistics. Rank {rank}, top {percentile:.1f} percent.</desc>
  <style>
    .title {{ font: 600 20px "Segoe UI", Ubuntu, sans-serif; fill: {theme["accent"]}; }}
    .label {{ font: 400 13px "Segoe UI", Ubuntu, sans-serif; fill: {theme["label"]}; }}
    .value {{ font: 600 14px "Segoe UI", Ubuntu, sans-serif; fill: {theme["value"]}; }}
    .rank-label {{ font: 600 11px "Segoe UI", Ubuntu, sans-serif; fill: {theme["label"]}; letter-spacing: 1px; }}
    .rank {{ font: 700 25px "Segoe UI", Ubuntu, sans-serif; fill: {theme["accent"]}; }}
    .rank-percentile {{ font: 400 11px "Segoe UI", Ubuntu, sans-serif; fill: {theme["label"]}; }}
    .footer {{ font: 400 11px "Segoe UI", Ubuntu, sans-serif; fill: {theme["footer"]}; }}
  </style>
  <rect x="0.5" y="0.5" width="759" height="234" rx="8" fill="{theme["background"]}" stroke="{theme["border"]}"/>
  <text x="28" y="38" class="title">{title}</text>
  <path d="M28 55.5H732" stroke="{theme["divider"]}"/>
  {render_column(left_stats, 32)}
  {render_column(right_stats, 327)}
  <path d="M617 72V194" stroke="{theme["divider"]}"/>
  <text x="686" y="79" class="rank-label" text-anchor="middle">RANK</text>
  <circle cx="686" cy="131" r="44" stroke="{theme["rank_track"]}" stroke-width="8"/>
  <circle cx="686" cy="131" r="44" stroke="{theme["accent"]}" stroke-width="8" stroke-linecap="round" stroke-dasharray="{rank_circumference:.2f}" stroke-dashoffset="{rank_offset:.2f}" transform="rotate(-90 686 131)"/>
  <text x="686" y="140" class="rank" text-anchor="middle">{rank}</text>
  <text x="686" y="194" class="rank-percentile" text-anchor="middle">Top {percentile:.1f}%</text>
  <path d="M28 207.5H732" stroke="{theme["divider"]}"/>
  <text x="28" y="224" class="footer">Updated daily · {updated_at}</text>
</svg>
'''


def aggregate_languages(
    repositories: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int], dict[str, str]]:
    code_sizes: dict[str, int] = {}
    repository_counts: dict[str, int] = {}
    colors: dict[str, str] = {}

    for repository in repositories:
        primary_language = repository.get("primary_language")
        if isinstance(primary_language, dict):
            name = primary_language.get("name")
            if isinstance(name, str):
                repository_counts[name] = repository_counts.get(name, 0) + 1
                colors.setdefault(
                    name, language_color(name, primary_language.get("color"))
                )

        languages = repository.get("languages", [])
        if not isinstance(languages, list):
            continue
        for language in languages:
            if not isinstance(language, dict):
                continue
            name = language.get("name")
            size = language.get("size")
            if not isinstance(name, str) or not isinstance(size, int) or size <= 0:
                continue
            code_sizes[name] = code_sizes.get(name, 0) + size
            colors.setdefault(name, language_color(name, language.get("color")))

    return code_sizes, repository_counts, colors


def compact_languages(
    values: dict[str, int],
    colors: dict[str, str],
    other_color: str,
    limit: int = 6,
) -> list[tuple[str, int, str]]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0].casefold()))
    if len(ordered) <= limit:
        return [(name, value, colors[name]) for name, value in ordered]

    visible = [(name, value, colors[name]) for name, value in ordered[: limit - 1]]
    other_value = sum(value for _, value in ordered[limit - 1 :])
    visible.append(("Other", other_value, other_color))
    return visible


def format_percentage(value: int, total: int) -> str:
    if total <= 0:
        return "0%"
    percentage = value / total * 100
    if 0 < percentage < 0.1:
        return "<0.1%"
    return f"{percentage:.1f}%"


def render_language_card(
    repositories: list[dict[str, Any]], theme_name: str
) -> str:
    theme = THEMES[theme_name]
    code_sizes, repository_counts, colors = aggregate_languages(repositories)
    code_total = sum(code_sizes.values())
    repository_total = sum(repository_counts.values())
    code_languages = compact_languages(
        code_sizes, colors, theme["label"], limit=6
    )
    repository_languages = compact_languages(
        repository_counts, colors, theme["label"], limit=6
    )

    if code_languages:
        bar_parts = []
        current_x = 28.0
        for index, (_, value, color) in enumerate(code_languages):
            width = 326 * value / code_total
            if index == len(code_languages) - 1:
                width = 354 - current_x
            bar_parts.append(
                f'<rect x="{current_x:.2f}" y="91" width="{width:.2f}" '
                f'height="11" fill="{color}"/>'
            )
            current_x += width
        code_bar = "".join(bar_parts)
        legend_parts = []
        for index, (name, value, color) in enumerate(code_languages):
            column = index % 2
            row = index // 2
            x = 32 + column * 166
            y = 132 + row * 31
            legend_parts.append(
                f'<circle cx="{x}" cy="{y - 4}" r="4" fill="{color}"/>'
                f'<text x="{x + 12}" y="{y}" class="label">{escape(name)}</text>'
                f'<text x="{x + 148}" y="{y}" class="value" text-anchor="end">'
                f'{escape(format_percentage(value, code_total))}</text>'
            )
        code_legend = "".join(legend_parts)
    else:
        code_bar = ""
        code_legend = (
            '<text x="28" y="132" class="label">No language data available</text>'
        )

    repository_parts = []
    maximum_repository_count = max(repository_counts.values(), default=1)
    for index, (name, count, color) in enumerate(repository_languages):
        y = 104 + index * 24
        bar_width = 150 * count / maximum_repository_count
        repository_parts.append(
            f'<text x="407" y="{y}" class="label">{escape(name)}</text>'
            f'<rect x="535" y="{y - 8}" width="150" height="8" rx="4" '
            f'fill="{theme["rank_track"]}"/>'
            f'<rect x="535" y="{y - 8}" width="{bar_width:.2f}" height="8" '
            f'rx="4" fill="{color}"/>'
            f'<text x="724" y="{y}" class="value" text-anchor="end">{count}</text>'
        )
    if not repository_parts:
        repository_parts.append(
            '<text x="407" y="104" class="label">No repository data available</text>'
        )

    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    leading_code_language = code_languages[0][0] if code_languages else "unknown"
    leading_repository_language = (
        repository_languages[0][0] if repository_languages else "unknown"
    )

    return f'''<svg width="760" height="270" viewBox="0 0 760 270" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc">
  <title id="title">Language Overview</title>
  <desc id="desc">Daily language statistics by code size and repository count. Leading languages: {escape(leading_code_language)} by code size and {escape(leading_repository_language)} by repository count.</desc>
  <style>
    .title {{ font: 600 20px "Segoe UI", Ubuntu, sans-serif; fill: {theme["accent"]}; }}
    .section {{ font: 600 11px "Segoe UI", Ubuntu, sans-serif; fill: {theme["label"]}; letter-spacing: 1px; }}
    .label {{ font: 400 13px "Segoe UI", Ubuntu, sans-serif; fill: {theme["label"]}; }}
    .value {{ font: 600 13px "Segoe UI", Ubuntu, sans-serif; fill: {theme["value"]}; }}
    .footer {{ font: 400 11px "Segoe UI", Ubuntu, sans-serif; fill: {theme["footer"]}; }}
  </style>
  <rect x="0.5" y="0.5" width="759" height="269" rx="8" fill="{theme["background"]}" stroke="{theme["border"]}"/>
  <text x="28" y="38" class="title">Language Overview</text>
  <path d="M28 55.5H732" stroke="{theme["divider"]}"/>
  <text x="28" y="78" class="section">BY CODE SIZE</text>
  <rect x="28" y="91" width="326" height="11" rx="5.5" fill="{theme["rank_track"]}"/>
  <g clip-path="url(#code-size-bar)">{code_bar}</g>
  <defs><clipPath id="code-size-bar"><rect x="28" y="91" width="326" height="11" rx="5.5"/></clipPath></defs>
  {code_legend}
  <path d="M380 72V226" stroke="{theme["divider"]}"/>
  <text x="407" y="78" class="section">BY REPOSITORY COUNT</text>
  {''.join(repository_parts)}
  <path d="M28 242.5H732" stroke="{theme["divider"]}"/>
  <text x="28" y="260" class="footer">Based on {repository_total} public repositories with detected languages · Updated daily · {updated_at}</text>
</svg>
'''


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a static GitHub profile card")
    parser.add_argument("--username", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dark-output", type=Path)
    parser.add_argument("--language-output", type=Path)
    parser.add_argument("--dark-language-output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2

    try:
        profile = request_json(
            f"{API_ROOT}/users/{quote(arguments.username)}", token
        )
        if not isinstance(profile, dict):
            raise GitHubAPIError("GitHub profile response was invalid")
        owned_repositories = fetch_repositories(arguments.username, token)
        affiliated_repositories = fetch_affiliated_repositories(arguments.username, token)
        repositories = affiliated_repositories or owned_repositories
        since = (datetime.now(timezone.utc) - timedelta(days=365)).date().isoformat()
        commits = fetch_search_count(
            f"author:{arguments.username} author-date:>={since}", "commits", token
        )
        pull_requests = fetch_search_count(
            f"author:{arguments.username} type:pr", "issues", token
        )
        issues = fetch_search_count(
            f"author:{arguments.username} type:issue", "issues", token
        )
        reviews = fetch_search_count(
            f"reviewed-by:{arguments.username} type:pr", "issues", token
        )
        cards = [(arguments.output, "light")]
        if arguments.dark_output:
            cards.append((arguments.dark_output, "dark"))
        rendered_cards = [
            (
                output,
                render_card(
                    arguments.username,
                    profile,
                    repositories,
                    commits,
                    pull_requests,
                    issues,
                    reviews,
                    theme_name,
                ),
            )
            for output, theme_name in cards
        ]
        language_cards = []
        if arguments.language_output:
            language_cards.append((arguments.language_output, "light"))
        if arguments.dark_language_output:
            language_cards.append((arguments.dark_language_output, "dark"))
        if language_cards:
            public_repositories = fetch_repositories(
                arguments.username, token, repository_type="all"
            )
            public_repositories = add_contributed_public_repositories(
                public_repositories, arguments.username, token
            )
            language_repositories = fetch_language_repositories(
                public_repositories, token
            )
            rendered_cards.extend(
                (
                    output,
                    render_language_card(language_repositories, theme_name),
                )
                for output, theme_name in language_cards
            )
    except GitHubAPIError as error:
        print(f"::error::{error}")
        return 1

    for output, card in rendered_cards:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output.with_suffix(output.suffix + ".tmp")
        temporary_output.write_text(card, encoding="utf-8")
        temporary_output.replace(output)
        print(f"Generated {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
