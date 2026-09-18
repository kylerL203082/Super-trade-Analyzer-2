#!/usr/bin/env python3

import csv
import io
import json
import math
import re
import statistics
import unicodedata
import urllib.request

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "index.html"
OUTPUT_PATH = ROOT / "tradeforge-data.json"

NOW = datetime.now(timezone.utc)

SEASON = NOW.year if NOW.month >= 3 else NOW.year - 1


URLS = {
    "stats": f"https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{SEASON}.csv",
    "players": "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv",
    "injuries": f"https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{SEASON}.csv",
    "snaps": f"https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{SEASON}.csv",
}


SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}


ALIASES = {
    "kennethgainwell": "kennygainwell",
    "kennygainwell": "kennygainwell",
    "joshpalmer": "joshuapalmer",
    "joshuapalmer": "joshuapalmer",
}


def clamp(value, low, high):
    return max(low, min(high, value))


def round1(value):
    return round(float(value) + 1e-12, 1)


def number(value, default=0.0):
    try:
        if value is None or str(value).strip() == "":
            return default

        result = float(value)

        if not math.isfinite(result):
            return default

        return result

    except Exception:
        return default


def normalize_name(value):
    value = unicodedata.normalize("NFKD", str(value or ""))

    value = "".join(
        char
        for char in value
        if not unicodedata.combining(char)
    )

    value = value.lower()

    value = re.sub(
        r"\b(jr|sr|ii|iii|iv)\b",
        "",
        value
    )

    value = re.sub(
        r"[^a-z0-9]",
        "",
        value
    )

    return ALIASES.get(value, value)


def fetch_text(url, required=True):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TradeForgeFantasy/1.0",
            "Accept": "text/csv,text/plain,*/*",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=90
        ) as response:
            return response.read().decode("utf-8-sig")

    except Exception as error:
        if required:
            raise RuntimeError(
                f"Could not download {url}: {error}"
            ) from error

        print(
            f"WARNING: optional feed unavailable: {url}: {error}"
        )

        return ""


def csv_rows(text):
    if not text.strip():
        return []

    return list(
        csv.DictReader(
            io.StringIO(text)
        )
    )


def parse_baseline_players(html):
    pattern = re.compile(
        r'\{\s*rank:\s*(\d+),\s*'
        r'pos:\s*"([^"]+)",\s*'
        r'name:\s*"([^"]+)",\s*'
        r'redraft:\s*([\d.]+),\s*'
        r'keeper:\s*([\d.]+),\s*'
        r'dynasty:\s*([\d.]+)\s*\}'
    )

    players = []

    for match in pattern.finditer(html):
        players.append({
            "rank": int(match.group(1)),
            "pos": match.group(2),
            "name": match.group(3),
            "redraft": float(match.group(4)),
            "keeper": float(match.group(5)),
            "dynasty": float(match.group(6)),
        })

    if len(players) < 300:
        raise RuntimeError(
            "Expected 300+ baseline players; "
            f"found {len(players)}"
        )

    names = [
        normalize_name(player["name"])
        for player in players
    ]

    if len(names) != len(set(names)):
        raise RuntimeError(
            "Duplicate normalized player names found."
        )

    return sorted(
        players,
        key=lambda player: player["rank"]
    )


def weighted_average(values, weights):
    if not values:
        return 0.0

    selected_weights = weights[-len(values):]

    total_weight = sum(selected_weights)

    return sum(
        value * weight
        for value, weight
        in zip(values, selected_weights)
    ) / total_weight


def parse_pct(value):
    text = str(value or "").strip()

    if not text:
        return None

    try:
        if text.endswith("%"):
            return clamp(
                float(text[:-1]) / 100,
                0,
                1
            )

        result = float(text)

        if result > 1.5:
            result /= 100

        return clamp(result, 0, 1)

    except Exception:
        return None


def make_curve(players, mode, position):
    return sorted(
        [
            player[mode]
            for player in players
            if player["pos"] == position
        ],
        reverse=True
    )


def value_from_rank(curve, rank):
    if not curve or rank is None or rank < 1:
        return None

    index = rank - 1

    if index < len(curve):
        return curve[index]

    tail = curve[-1]

    return max(
        0.1,
        tail
        * math.exp(
            -(index - len(curve) + 1) / 18
        )
    )


def make_rank_map(metrics, field, position):
    ordered = sorted(
        [
            metric
            for metric in metrics.values()
            if (
                metric["pos"] == position
                and metric.get(field) is not None
            )
        ],
        key=lambda item: (
            -item[field],
            item["name"]
        )
    )

    return {
        metric["key"]: index + 1
        for index, metric
        in enumerate(ordered)
    }


def opportunity_raw(row):
    position = str(
        row.get("position") or ""
    ).upper()

    carries = number(row.get("carries"))
    targets = number(row.get("targets"))
    attempts = number(row.get("attempts"))
    target_share = number(row.get("target_share"))
    air_share = number(row.get("air_yards_share"))
    wopr = number(row.get("wopr"))

    if position == "QB":
        return attempts + 2.0 * carries

    if position == "RB":
        return (
            carries
            + 1.75 * targets
            + 5.0 * target_share
        )

    if position in {"WR", "TE"}:
        return (
            2.0 * targets
            + 8.0 * target_share
            + 3.0 * air_share
            + 2.0 * wopr
        )

    return carries + targets


def latest_injury_by_name(rows):
    output = {}

    for row in rows:
        name = normalize_name(
            row.get("full_name")
        )

        if not name:
            continue

        week = int(
            number(
                row.get("week"),
                0
            )
        )

        modified = (
            row.get("date_modified")
            or ""
        )

        sort_key = (
            week,
            modified
        )

        if (
            name not in output
            or sort_key > output[name][0]
        ):
            output[name] = (
                sort_key,
                row
            )

    return {
        key: value[1]
        for key, value
        in output.items()
    }


def availability_factor(injury, player_meta):
    status = str(
        (player_meta or {}).get("status") or ""
    ).upper()

    if status in {"RES", "PUP", "RSN"}:
        return 0.78

    if status == "SUS":
        return 0.84

    if status in {"CUT", "UFA", "RET"}:
        return 0.62

    if status in {"INA", "EXE"}:
        return 0.92

    report = str(
        (injury or {}).get("report_status") or ""
    ).lower()

    practice = str(
        (injury or {}).get("practice_status") or ""
    ).lower()

    if "out" in report:
        return 0.94

    if "doubt" in report:
        return 0.97

    if "question" in report:
        return 0.99

    if (
        "did not" in practice
        or practice in {
            "dnp",
            "did not participate"
        }
    ):
        return 0.985

    if "limited" in practice:
        return 0.995

    return 1.0


def age_from_meta(meta):
    if not meta:
        return None

    age = number(
        meta.get("age"),
        float("nan")
    )

    if math.isfinite(age) and age > 0:
        return age

    birth_date = (
        meta.get("birth_date")
        or meta.get("birthdate")
    )

    if not birth_date:
        return None

    try:
        birthday = datetime.fromisoformat(
            str(birth_date)[:10]
        )

        return round(
            (
                NOW.replace(tzinfo=None)
                - birthday
            ).days
            / 365.2425,
            2
        )

    except Exception:
        return None


def draft_capital_score(meta, fallback):
    if not meta:
        return fallback

    overall = number(
        meta.get("draft_number")
        or meta.get("draft_pick")
        or meta.get("draft_ovr")
        or meta.get("draft_overall"),
        0
    )

    round_number = number(
        meta.get("draft_round"),
        0
    )

    if overall > 0:
        if overall <= 10:
            return 98 - (overall - 1) * 1.3

        if overall <= 32:
            return 85 - (overall - 11) * 0.7

        if overall <= 64:
            return 70 - (overall - 33) * 0.45

        if overall <= 100:
            return 56 - (overall - 65) * 0.30

        if overall <= 150:
            return 44 - (overall - 101) * 0.16

        if overall <= 220:
            return 35 - (overall - 151) * 0.10

        return 25

    if round_number > 0:
        return {
            1: 88,
            2: 72,
            3: 58,
            4: 46,
            5: 38,
            6: 32,
            7: 27,
        }.get(
            int(round_number),
            25
        )

    return fallback


def load_previous_feed():
    if not OUTPUT_PATH.exists():
        return {}

    try:
        return json.loads(
            OUTPUT_PATH.read_text(
                encoding="utf-8"
            )
        )

    except Exception:
        return {}


def main():
    print("Starting TradeForge data update...")

    baseline = parse_baseline_players(
        INDEX_PATH.read_text(
            encoding="utf-8"
        )
    )

    print(
        f"Found {len(baseline)} baseline players."
    )

    previous = load_previous_feed()

    print("Downloading nflverse player stats...")

    stats = csv_rows(
        fetch_text(
            URLS["stats"],
            required=True
        )
    )

    print("Downloading nflverse player metadata...")

    players = csv_rows(
        fetch_text(
            URLS["players"],
            required=True
        )
    )

    print("Downloading injury data...")

    injuries = csv_rows(
        fetch_text(
            URLS["injuries"],
            required=False
        )
    )

    print("Downloading snap counts...")

    snaps = csv_rows(
        fetch_text(
            URLS["snaps"],
            required=False
        )
    )

    stats = [
        row
        for row in stats
        if str(
            row.get("season_type") or "REG"
        ).upper()
        == "REG"
    ]

    completed_week = max(
        [
            int(
                number(
                    row.get("week"),
                    0
                )
            )
            for row in stats
        ]
        or [0]
    )

    injury_week = max(
        [
            int(
                number(
                    row.get("week"),
                    0
                )
            )
            for row in injuries
        ]
        or [0]
    )

    current_week = max(
        completed_week,
        injury_week
    )

    meta_by_name = {}

    for row in players:
        display_name = (
            row.get("display_name")
            or row.get("full_name")
            or row.get("player_name")
        )

        name_key = normalize_name(
            display_name
        )

        if name_key:
            meta_by_name[name_key] = row

    injury_by_name = latest_injury_by_name(
        injuries
    )

    snap_by_name = defaultdict(list)

    for row in snaps:
        name_key = normalize_name(
            row.get("player")
        )

        if name_key:
            snap_by_name[name_key].append(row)

    for rows in snap_by_name.values():
        rows.sort(
            key=lambda row: int(
                number(
                    row.get("week"),
                    0
                )
            )
        )

    stats_by_name = defaultdict(list)

    for row in stats:
        player_name = (
            row.get("player_display_name")
            or row.get("player_name")
        )

        name_key = normalize_name(
            player_name
        )

        position = str(
            row.get("position") or ""
        ).upper()

        if (
            name_key
            and position in SKILL_POSITIONS
        ):
            stats_by_name[name_key].append(row)

    for rows in stats_by_name.values():
        rows.sort(
            key=lambda row: int(
                number(
                    row.get("week"),
                    0
                )
            )
        )

    metrics = {}

    week_weights = [
        0.10,
        0.20,
        0.30,
        0.40
    ]

    for name_key, rows in stats_by_name.items():
        if not rows:
            continue

        position = str(
            rows[-1].get("position") or ""
        ).upper()

        fantasy_points = [
            number(
                row.get(
                    "fantasy_points_ppr"
                )
            )
            for row in rows
        ]

        recent_rows = rows[-4:]

        recent_ppr = weighted_average(
            [
                number(
                    row.get(
                        "fantasy_points_ppr"
                    )
                )
                for row in recent_rows
            ],
            week_weights
        )

        recent_opportunity = weighted_average(
            [
                opportunity_raw(row)
                for row in recent_rows
            ],
            week_weights
        )

        season_ppg = (
            statistics.fmean(
                fantasy_points
            )
            if fantasy_points
            else 0
        )

        snap_rows = (
            snap_by_name
            .get(
                name_key,
                []
            )
            [-4:]
        )

        snap_pcts = [
            parse_pct(
                row.get(
                    "offense_pct"
                )
            )
            for row in snap_rows
        ]

        snap_pcts = [
            value
            for value in snap_pcts
            if value is not None
        ]

        snap_avg = (
            statistics.fmean(
                snap_pcts
            )
            if snap_pcts
            else None
        )

        snap_std = (
            statistics.pstdev(
                snap_pcts
            )
            if len(snap_pcts) > 1
            else 0
        )

        metrics[name_key] = {
            "key": name_key,
            "name": (
                rows[-1].get(
                    "player_display_name"
                )
                or rows[-1].get(
                    "player_name"
                )
            ),
            "pos": position,
            "team": (
                rows[-1].get("team")
                or rows[-1].get(
                    "recent_team"
                )
            ),
            "games": len(rows),
            "season_ppg": season_ppg,
            "recent_ppr": recent_ppr,
            "opportunity_raw": recent_opportunity,
            "snap_avg": snap_avg,
            "snap_std": snap_std,
        }

    curves = {
        position: {
            "redraft": make_curve(
                baseline,
                "redraft",
                position
            ),
            "dynasty": make_curve(
                baseline,
                "dynasty",
                position
            ),
        }
        for position in SKILL_POSITIONS
    }

    rank_maps = {}

    for position in SKILL_POSITIONS:
        rank_maps[position] = {
            "season": make_rank_map(
                metrics,
                "season_ppg",
                position
            ),
            "recent": make_rank_map(
                metrics,
                "recent_ppr",
                position
            ),
            "opportunity": make_rank_map(
                metrics,
                "opportunity_raw",
                position
            ),
        }

    output = {}

    updated = 0
    matched_meta = 0
    matched_injuries = 0

    for base in baseline:
        name_key = normalize_name(
            base["name"]
        )

        player_metrics = metrics.get(
            name_key
        )

        meta = meta_by_name.get(
            name_key
        )

        injury = injury_by_name.get(
            name_key
        )

        if meta:
            matched_meta += 1

        if injury:
            matched_injuries += 1

        if (
            not player_metrics
            or base["pos"]
            not in SKILL_POSITIONS
        ):
            continue

        position = base["pos"]

        season_rank = (
            rank_maps[position]["season"]
            .get(name_key)
        )

        recent_rank = (
            rank_maps[position]["recent"]
            .get(name_key)
        )

        opportunity_rank = (
            rank_maps[position]["opportunity"]
            .get(name_key)
        )

        season_value = (
            value_from_rank(
                curves[position]["redraft"],
                season_rank
            )
            or base["redraft"]
        )

        recent_value = (
            value_from_rank(
                curves[position]["redraft"],
                recent_rank
            )
            or season_value
        )

        opportunity_value = (
            value_from_rank(
                curves[position]["redraft"],
                opportunity_rank
            )
            or season_value
        )

        projection = clamp(
            0.45 * recent_value
            + 0.30 * season_value
            + 0.25 * opportunity_value,
            0.1,
            100
        )

        data_weight = min(
            0.75,
            player_metrics["games"] / 8
        )

        tradeforge_anchor = (
            base["redraft"]
            * (1 - data_weight)
            + season_value
            * data_weight
        )

        snap_avg = (
            player_metrics["snap_avg"]
            if player_metrics["snap_avg"]
            is not None
            else 0.65
        )

        snap_std = (
            player_metrics["snap_std"]
            or 0
        )

        role_confidence = clamp(
            48
            + 38 * snap_avg
            - 20 * snap_std
            + min(
                9,
                player_metrics["games"]
                * 1.5
            ),
            50,
            95
        )

        upside = clamp(
            max(
                projection,
                recent_value * 1.05,
                opportunity_value * 1.03
            ),
            0.1,
            100
        )

        availability = availability_factor(
            injury,
            meta
        )

        previous_player = (
            previous
            .get("players", {})
            .get(name_key, {})
        )

        previous_projection = (
            previous_player
            .get("engine", {})
            .get("redraft", {})
            .get("projection")
        )

        market_change = 0

        if (
            isinstance(
                previous_projection,
                (int, float)
            )
            and previous_projection > 0
        ):
            percent_move = (
                projection
                - previous_projection
            ) / max(
                previous_projection,
                10
            )

            market_change = clamp(
                percent_move * 0.20,
                -0.03,
                0.03
            )

        age = age_from_meta(meta)

        draft_score = draft_capital_score(
            meta,
            base["dynasty"]
        )

        draft_year = int(
            number(
                (meta or {}).get(
                    "draft_year"
                ),
                0
            )
        )

        is_rookie = (
            draft_year == SEASON
            if draft_year
            else False
        )

        dynasty_anchor = clamp(
            0.65 * base["dynasty"]
            + 0.35 * projection,
            0.1,
            100
        )

        year1 = projection

        year2 = clamp(
            0.70 * dynasty_anchor
            + 0.30 * projection,
            0.1,
            100
        )

        year3 = clamp(
            0.82 * dynasty_anchor
            + 0.18 * base["dynasty"],
            0.1,
            100
        )

        dynasty_consensus = clamp(
            0.72 * base["dynasty"]
            + 0.28 * projection,
            0.1,
            100
        )

        market_momentum = clamp(
            dynasty_consensus
            * (1 + market_change),
            0.1,
            100
        )

        redraft_engine = {
            "projection": round1(
                projection
            ),
            "consensus": round1(
                tradeforge_anchor
            ),
            "opportunity": round1(
                opportunity_value
            ),
            "recent": round1(
                recent_value
            ),
            "availability": round(
                availability,
                3
            ),
            "roleConfidence": round1(
                role_confidence
            ),
            "upside": round1(
                upside
            ),
            "marketChange": round(
                market_change,
                4
            ),
        }

        dynasty_engine = {
            "year1": round1(year1),
            "year2": round1(year2),
            "year3": round1(year3),
            "consensus": round1(
                dynasty_consensus
            ),
            "roleSecurity": round1(
                role_confidence
            ),
            "prospect": round1(
                draft_score
                if is_rookie
                else dynasty_consensus
            ),
            "draftCapital": round1(
                draft_score
            ),
            "marketMomentum": round1(
                market_momentum
            ),
            "opportunity": round1(
                opportunity_value
            ),
            "isRookie": bool(
                is_rookie
            ),
            "nflGames": int(
                player_metrics["games"]
            ),
        }

        if age is not None:
            dynasty_engine["age"] = round(
                age,
                2
            )

        output[name_key] = {
            "name": base["name"],
            "pos": position,
            "team": player_metrics.get(
                "team"
            ),
            "engine": {
                "redraft": redraft_engine,
                "dynasty": dynasty_engine,
            },
            "source": {
                "season": SEASON,
                "lastCompletedWeek": completed_week,
                "games": int(
                    player_metrics["games"]
                ),
                "seasonPPRPerGame": round1(
                    player_metrics["season_ppg"]
                ),
                "recentWeightedPPR": round1(
                    player_metrics["recent_ppr"]
                ),
                "recentOpportunityIndex": round1(
                    player_metrics[
                        "opportunity_raw"
                    ]
                ),
                "snapShare": (
                    round(
                        player_metrics["snap_avg"],
                        3
                    )
                    if player_metrics["snap_avg"]
                    is not None
                    else None
                ),
                "injuryStatus": (
                    injury or {}
                ).get(
                    "report_status"
                ) or None,
                "practiceStatus": (
                    injury or {}
                ).get(
                    "practice_status"
                ) or None,
                "rosterStatus": (
                    meta or {}
                ).get(
                    "status"
                ) or None,
            },
        }

        updated += 1

    payload = {
        "meta": {
            "engineVersion":
            "1.1-nflverse",

            "generatedAt":
            NOW.isoformat(),

            "season":
            SEASON,

            "week":
            current_week,

            "lastCompletedWeek":
            completed_week,

            "mode":
            "nflverse-no-key",

            "baselinePlayers":
            len(baseline),

            "autoUpdatedPlayers":
            updated,

            "matchedPlayerMetadata":
            matched_meta,

            "matchedInjuries":
            matched_injuries,

            "sources": [
                "nflverse weekly player stats",
                "nflverse players",
                "nflverse injuries",
                "nflverse snap counts",
            ],

            "notes": [
                "QB/RB/WR/TE players with current-season NFL stats receive live engine inputs.",
                "Players without sufficient NFL data retain the hard-coded TradeForge baseline.",
                "Kickers and defenses retain baseline values in v1.1.",
                "Market movement is TradeForge's own week-to-week projection change.",
            ],
        },
        "players": output,
    }

    OUTPUT_PATH.write_text(
        json.dumps(
            payload,
            indent=2
        )
        + "\n",
        encoding="utf-8"
    )

    print(
        "TradeForge feed built successfully."
    )

    print(
        f"Updated players: {updated}"
    )

    print(
        f"Baseline players: {len(baseline)}"
    )

    print(
        f"Season: {SEASON}"
    )

    print(
        f"Completed week: {completed_week}"
    )


if __name__ == "__main__":
    main()
