"""
Optional claim-level review by a language model, audited like Anchor's
faithfulness judge.

The model is never asked "how credible is this, 1-10". It has to:

  1. list the article's key factual claims (in the article's language)
  2. for each, cite the id of a retrieved passage from OTHER outlets that
     supports or contradicts it, or mark it unverified
  3. say whether the headline is a fair summary of the story

Scores are derived from that audit, and a citation of a passage that was
not given to the model is downgraded to unverified. Any provider Anchor
supports works (Gemini and Groq have free tiers); the key comes from the
environment, exactly as in Anchor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import Article
from ..retrieve import Hit
from .signals import Signal

PROMPT_VERSION = "review-v1"
VERDICTS = ("supported", "contradicted", "unverified")

SYSTEM = """You audit a news article against coverage of the same story from other outlets.
The article and passages may be in Nepali, English or both; compare meaning, not wording.

1. List up to 8 key factual claims the ARTICLE makes (who did what, numbers, dates, causes).
   Write each claim in the article's own language. Skip opinions.
2. For each claim give exactly one verdict, judged ONLY against the PASSAGES:
   - "supported": a passage states the same fact. passage_id = that passage's id, copied exactly.
   - "contradicted": a passage states an incompatible fact (different number, actor, date, outcome).
     passage_id = that passage's id.
   - "unverified": no passage says it either way. passage_id = null.
   note: a few words when the verdict is not "supported".
3. headline_fair: true if the headline is a fair summary of the article body, false if it
   exaggerates, adds facts the body lacks, or misleads. headline_note: one sentence why.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "claim": {"type": "string"},
                "verdict": {"type": "string", "enum": list(VERDICTS)},
                "passage_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["claim", "verdict", "passage_id", "note"],
            "additionalProperties": False,
        }},
        "headline_fair": {"type": "boolean"},
        "headline_note": {"type": "string"},
    },
    "required": ["claims", "headline_fair", "headline_note"],
    "additionalProperties": False,
}


@dataclass
class ClaimCheck:
    claim: str
    verdict: str
    passage_id: Optional[str]
    note: Optional[str] = None


@dataclass
class Review:
    signal: Signal
    claims: list[ClaimCheck] = field(default_factory=list)
    headline_fair: Optional[bool] = None
    headline_note: str = ""
    model: str = ""
    error: Optional[str] = None


def build_prompt(article: Article, passages: list[Hit], max_body_chars: int = 6000) -> str:
    lines = [f"HEADLINE: {article.title}", "", "ARTICLE:", article.body[:max_body_chars], "", "PASSAGES:"]
    for h in passages:
        lines.append(f"[{h.chunk.id}] {h.chunk.text}")
    if not passages:
        lines.append("(none)")
    return "\n".join(lines)


def review(article: Article, passages: list[Hit], connection=None) -> Review:
    """connection: an anchor.llm.Connection, or None to read one from the
    environment. Returns a Review with error set (and no claims) when no
    model is available or the call fails; the rest of the report stands."""
    try:
        from anchor import llm
    except ImportError:
        return Review(Signal("claims", 0.0, "LLM review unavailable (anchor not installed)."), error="anchor not installed")
    conn = connection or llm.connection_from_env()
    if conn is None:
        return Review(Signal("claims", 0.0, "LLM review skipped: no model key set."), error="no connection")
    try:
        data, _ = llm.complete_json(conn, SYSTEM, build_prompt(article, passages), SCHEMA, name="claim_review")
    except llm.LLMError as e:
        return Review(Signal("claims", 0.0, f"LLM review failed ({e.kind})."), model=conn.name, error=e.kind)
    allowed = {h.chunk.id for h in passages}
    claims = []
    for c in data["claims"]:
        verdict, pid = c["verdict"], c["passage_id"]
        if verdict != "unverified" and pid not in allowed:  # cited something it wasn't given
            verdict, pid, c["note"] = "unverified", None, "cited passage was not among those retrieved"
        claims.append(ClaimCheck(c["claim"], verdict, pid if verdict != "unverified" else None, c["note"]))
    n = len(claims) or 1
    contradicted = sum(c.verdict == "contradicted" for c in claims)
    unverified = sum(c.verdict == "unverified" for c in claims)
    concern = min(1.0, contradicted / n * 0.8 + unverified / n * 0.3 + (0.0 if data["headline_fair"] else 0.3))
    supported = len(claims) - contradicted - unverified
    summary = (f"{supported} of {len(claims)} claims supported by other coverage, {contradicted} contradicted, "
               f"{unverified} unverified." if claims else "No factual claims extracted.")
    evidence = [f"{c.verdict}: {c.claim}" + (f" [{c.passage_id}]" if c.passage_id else "") for c in claims]
    if not data["headline_fair"]:
        evidence.append("headline: " + data["headline_note"])
    return Review(Signal("claims", round(concern, 3), summary, evidence,
                         {"supported": supported, "contradicted": contradicted, "unverified": unverified}),
                  claims, data["headline_fair"], data["headline_note"], conn.name)
