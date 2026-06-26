#!/usr/bin/env python3
"""
biblioteksspanaren.py – söker tillgänglighet på Stockholms stadsbibliotek

INDATA (stdin):  JSON-lista med böcker
  [{"titel": "...", "forfattare": "...", "isbn": "..."}, ...]
  ISBN "N/A" eller "" → söker på titel + författare istället

UTDATA (stdout): JSON-objekt
  {"tillgangliga": [...], "ej_kontrollerade": [...]}

Framsteg loggas till stderr. Kör t.ex.:
  echo '[{"titel":"Den mörka skogen","forfattare":"Liu Cixin","isbn":"9189516249"}]' \\
    | python3 scripts/biblioteksspanaren.py

TEKNISKA DETALJER:
  - GraphQL-API: https://biblioteket.stockholm.se/graphql
  - Query: searchWithFilter med SearchWithFilterInput (fält: "query")
  - Tillgänglighetsstatus: AVAILABLE_FOR_LOAN = finns inne
  - Introspection är avstängd på API:et
"""

import json
import sys
import time
import urllib.error
import urllib.request

GRAPHQL_URL = "https://biblioteket.stockholm.se/graphql"

# Slug → visningsnamn för de fyra målfilialerna
TARGET_SLUGS = {
    "stadsbiblioteket": "Stadsbiblioteket (Odenplan)",
    "transtromerbiblioteket": "Tranströmerbiblioteket (Medborgarplatsen)",
    "bjorkhagens-bibliotek": "Björkhagens bibliotek",
    "bagarmossens-bibliotek": "Bagarmossens bibliotek",
}

GQL_QUERY = """
query searchWithFilter($filterQuery: SearchWithFilterInput!) {
  searchWithFilter(filterQuery: $filterQuery) {
    totalHits
    groupedMedia {
      mediaList {
        key
        title
        author
        holdings {
          branch { name slug }
          loanStatus
          nofTotalAvailableForLoan
        }
      }
    }
  }
}
"""

PAUSE_SECONDS = 2.5
RATE_LIMIT_WAIT = 10


def gql_search(search_text):
    payload = json.dumps({
        "query": GQL_QUERY,
        "variables": {"filterQuery": {"query": search_text}},
    }).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Origin": "https://biblioteket.stockholm.se",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def author_matches(expected, found):
    """
    Returnerar True om minst ett meningsfullt ord från förväntad författare
    förekommer i den hittade posten. Skyddar mot falska träffar vid titelsökning
    (t.ex. "Richard Swan" matchar fel "Richard Brautigan").
    """
    if not found:
        return True
    exp_words = {w for w in expected.lower().split() if len(w) > 2}
    found_lower = found.lower()
    return any(w in found_lower for w in exp_words)


def check_book(titel, forfattare, isbn):
    use_isbn = bool(isbn and isbn not in ("N/A", ""))
    search_term = isbn if use_isbn else f"{titel} {forfattare}"

    for attempt in range(2):
        try:
            result = gql_search(search_term)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                print(f"  >> 429 – väntar {RATE_LIMIT_WAIT}s...", file=sys.stderr)
                time.sleep(RATE_LIMIT_WAIT)
                continue
            return None, f"HTTP {e.code}"
        except Exception as e:
            return None, f"FEL: {e}"
    else:
        return None, "429 – överhoppad"

    if "errors" in result:
        return None, str(result["errors"])

    groups = result.get("data", {}).get("searchWithFilter", {}).get("groupedMedia", [])
    for group in groups:
        for media in group.get("mediaList") or []:
            key = media.get("key")
            if not key:
                continue
            # Vid titelsökning: verifiera att författaren rimligen stämmer
            if not use_isbn and not author_matches(forfattare, media.get("author", "")):
                continue
            available = [
                TARGET_SLUGS[h["branch"]["slug"]]
                for h in (media.get("holdings") or [])
                if (h.get("branch") or {}).get("slug") in TARGET_SLUGS
                and h.get("loanStatus") == "AVAILABLE_FOR_LOAN"
            ]
            if available:
                return {
                    "titel": media.get("title", titel),
                    "forfattare": media.get("author", forfattare),
                    "isbn": isbn if use_isbn else "",
                    "filialer": available,
                    "lank": f"https://biblioteket.stockholm.se/titel/{key}",
                }, None
    return None, "ej_funnen"


def main():
    books = json.load(sys.stdin)
    total = len(books)
    tillgangliga = []
    ej_kontrollerade = []

    for i, b in enumerate(books):
        titel = b.get("titel", "")
        forfattare = b.get("forfattare", "")
        isbn = b.get("isbn", "")
        print(f"[{i + 1}/{total}] {titel} – {forfattare} (ISBN: {isbn or '–'})", file=sys.stderr)

        found, err = check_book(titel, forfattare, isbn)
        if found:
            tillgangliga.append(found)
            print(f"  >> INNE: {', '.join(found['filialer'])}", file=sys.stderr)
        elif err == "ej_funnen":
            print("  >> Ej hittad i katalogen", file=sys.stderr)
        else:
            ej_kontrollerade.append({"titel": titel, "forfattare": forfattare, "status": err})
            print(f"  >> {err}", file=sys.stderr)

        if i < total - 1:
            time.sleep(PAUSE_SECONDS)

    print(f"\nKLART: {len(tillgangliga)}/{total} böcker finns inne", file=sys.stderr)
    json.dump(
        {"tillgangliga": tillgangliga, "ej_kontrollerade": ej_kontrollerade},
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    main()
