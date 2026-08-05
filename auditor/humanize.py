"""Plain-English labels for the sheet. Presentation only — no check logic.

The sheet is read by a QA person, not by us. `check=blank`, `issue=missing <h1>`, `suggestion=` is
three columns of nothing useful: the check name is our module name, the issue assumes you know what
an H1 is, and the column that should say what to DO is empty on 25% of rows.

This lives in the presentation layer ON PURPOSE. Rewriting the strings inside the checks would move
`checks_version`, which invalidates every brand's resume cache and re-keys findings — a cosmetic
change would cost a full re-crawl. The JSONL and CSV keep the technical values for anything that
consumes them; only the sheet gets translated.

Rule for every suggestion: say the fix in the reader's terms. If a defect genuinely has no
mechanical fix, say what to LOOK AT. Never leave it blank.
"""
from __future__ import annotations

# Our module names mean nothing to a QA reader.
CHECK_LABELS = {
    "blank": "Page content",
    "placeholder": "Unfilled template",
    "empty_slot": "Missing text",
    "enumeration": "Sitemap",
    "heading_structure": "Headings",
    "broken_links": "Links",
    "meta": "Search listing",
    "phone": "Phone numbers",
    "misspelling": "Spelling",
    "scope": "Wording",
    "spelling": "Spelling",
}

# (check, issue-prefix) -> (plain issue, suggestion). First match wins, so put specific before
# general. A suggestion of "" means the check already writes a good one and we keep it.
_RULES: list[tuple[str, str, str, str]] = [
    ("blank", "missing <h1>",
     "This page has no main heading.",
     "Every page needs one main heading that says what the page is about — usually the same as the "
     "page title. Add one at the top of the content."),
    ("blank", "",
     "This page has little or no visible content.",
     "Open the page and check the content actually loaded. If a section looks empty, its text field "
     "is probably blank in WordPress."),

    ("heading_structure", "empty heading",
     "There is a heading on the page with no words in it.",
     "An empty heading is usually a leftover from editing — a heading block that was cleared but "
     "not deleted. Delete the empty block, or put the intended wording back."),
    ("heading_structure", "multiple <h1>",
     "This page has more than one main heading.",
     "A page should have exactly one main heading. Keep the one that describes the page and change "
     "the others to sub-headings."),
    ("heading_structure", "duplicate H1 across pages",
     "Several pages share the same main heading.",
     "Two or more pages have identical main headings, so they look like the same page to Google. "
     "Give each one wording specific to that page or location."),
    ("heading_structure", "skipped level",
     "The headings jump a level (for example a main heading straight to a sub-sub-heading).",
     "Headings should step down one level at a time. Change the heading that jumps so it is the "
     "next level down from the one above it."),
    ("heading_structure", "template label leaked",
     "A template field name is showing as a heading instead of real wording.",
     "The heading is displaying an internal field name. Replace it with the wording that was meant "
     "to appear there."),
    ("heading_structure", "",
     "There is a problem with this page's heading structure.",
     "Open the page and check the headings read as a sensible outline, one level at a time."),

    ("meta", "missing meta description",
     "This page has no search-result description.",
     "The description is the grey text under your link in Google. Without it Google writes its own. "
     "Add one of about 150 characters in Rank Math describing the page."),
    ("meta", "title length out of bounds",
     "The search-result title is too long or too short.",
     "Aim for roughly 15-60 characters. Too long and Google cuts it off mid-word; too short wastes "
     "the space. Edit the SEO title in Rank Math."),
    ("meta", "duplicate title across pages",
     "Several pages share the same search-result title.",
     "Identical titles make these pages compete with each other in Google. Give each one a title "
     "specific to its topic or location."),
    ("meta", "duplicate meta description across pages",
     "Several pages share the same search-result description.",
     "Give each page its own description. Copies add no information and Google may ignore them."),
    ("meta", "repeated segment in title",
     "The search-result title repeats itself.",
     "Something like a brand or city name appears twice in the title. Remove the repeat."),
    ("meta", "",
     "There is a problem with this page's search listing.",
     "Check the SEO title and description for this page in Rank Math."),

    ("placeholder", "unresolved [acf field]",
     "A template placeholder is showing on the page instead of real content.",
     "Visitors can see raw template code such as [acf field=near-in]. The field it refers to is "
     "empty in WordPress — fill it in, or remove the placeholder from the template."),
    ("placeholder", "",
     "Template code is visible on the page.",
     "Open the page: raw template code is showing where real wording should be."),

    ("phone", "malformed tel: number",
     "The call button has a broken phone number in it.",
     "The number behind the call button is not a valid phone number, so tapping it may fail. Fix "
     "the tel: link on this page."),
    ("phone", "unknown phone number",
     "A phone number appears here that is not on the approved list.",
     "This number is not in the NAP sheet. Confirm whether it is a real current number — if it is, "
     "add it to the NAP sheet; if not, replace it with the correct one."),
    ("phone", "tel: href has URL-encoded",
     "The call button's number contains stray characters.",
     "The link behind the call button has encoded characters such as %20 in it. Rewrite it as "
     "digits only, e.g. tel:8885551234."),
    ("phone", "displayed number differs",
     "The number shown on the page is not the number the call button dials.",
     "A visitor reads one number and their phone dials a different one. Check which is correct and "
     "make both match."),
    ("phone", "tel: href != displayed",
     "The number shown on the page is not the number the call button dials.",
     "A visitor reads one number and their phone dials a different one. Check which is correct and "
     "make both match."),
    ("phone", "", "", ""),          # the rest already explain themselves well

    ("enumeration", "indexable page missing from sitemap",
     "This page is live and public but is not listed in the sitemap.",
     "Google uses the sitemap to find pages. This one is missing, so it may not get indexed. Either "
     "add it to the sitemap or, if it should not be public, mark it noindex."),
    ("enumeration", "noindex page missing from sitemap",
     "This page is hidden from search and also absent from the sitemap.",
     "This is usually correct and needs no action — it is listed so you can confirm nothing you "
     "wanted ranking was hidden by mistake."),
    ("enumeration", "sitemap page returns HTTP 404",
     "The sitemap lists a page that no longer exists.",
     "Remove the dead URL from the sitemap, or restore the page if it was deleted by mistake."),
    ("enumeration", "sitemap page returns HTTP 500",
     "A page in the sitemap is returning a server error.",
     "The page is listed but the server fails to load it. Ask the developer to check the error."),
    ("enumeration", "sitemap page could not be fetched",
     "A page in the sitemap did not respond in time.",
     "This may be a temporary slowdown. If it repeats on the next run, ask the developer to check."),
    ("enumeration", "in WP-REST but returns HTTP 404",
     "WordPress lists this page but visitors get a 'not found' error.",
     "The page exists in WordPress but is not reachable publicly. Check its status and permalink."),
    ("enumeration", "",
     "There is a mismatch between this page and the sitemap.",
     "Compare what is live against what the sitemap lists."),

    ("broken_links", "external link unverified",
     "A link to another website could not be checked automatically.",
     "The other site blocked our automated check — this often means the link is fine. Click it "
     "yourself to confirm it still works."),
    ("broken_links", "malformed / truncated URL",
     "A link on this page is broken or incomplete.",
     "The web address is malformed, so clicking it goes nowhere. Fix the link."),
    ("broken_links", "unreachable",
     "A link on this page did not respond.",
     "The page it points to did not load. Click it to confirm, then fix or remove it."),
    ("broken_links", "",
     "", ""),                       # 404/403/staging already read clearly

    ("empty_slot", "", "", ""),      # already writes a full explanation
    ("misspelling", "", "", ""),     # already writes the correction
    ("scope", "", "", ""),           # already writes the explanation
]


def check_label(check: str) -> str:
    return CHECK_LABELS.get(check, check)


def _match(check: str, issue: str):
    for c, prefix, plain, sug in _RULES:
        if c == check and (not prefix or issue.startswith(prefix)):
            return plain, sug
    return "", ""


def plain_issue(check: str, issue: str) -> str:
    """Reader-facing wording. Falls back to the original when we have nothing better."""
    plain, _ = _match(check, issue or "")
    return plain or issue or "See the details on this row."


def suggestion_for(check: str, issue: str, existing: str) -> str:
    """The check's own suggestion when it wrote one, otherwise ours. NEVER empty."""
    if (existing or "").strip():
        return existing
    _, sug = _match(check, issue or "")
    return sug or ("Open the page and check this. If it is not clear what to change, send this row "
                   "to Syed.")
