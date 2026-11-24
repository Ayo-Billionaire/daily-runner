"""Banned words for coach outreach (24 Nov 2026). Applies to emails, DMs and follow ups.

NEVER  = any hit blocks the text. Built from: Meta Advertising Standards, Health and Wellness (transparency.meta.com, read 24 Nov 2026: no claims to
         cure, heal or eliminate incurable diseases; no clickbait with promised outcomes in a set time; no statements of inferiority about appearance),
         FTC business and coaching opportunity guidance (consumer.ftc.gov, 22 Aug 2023: baseless claims about how much money buyers can make),
         the ICF position that coaching is not therapy, and the spam word groups from the Aerosend 2026 guide (financial, urgency, hype, shady).
AVOID  = salesy spam filter words (same guide) that are fine alone but raise risk in a cluster. Two or more in one text blocks it; one is allowed. Common plain words from that guide (only, now, today, save, offer, best, success) are left out on purpose: they block normal sentences and the guide says no single word decides placement.
NICHE  = words and openings Ayo or coaches rejected (his corrections of 12 and 17 Nov 2026, humanize blacklist). Any hit blocks the text.
The one word the offer needs, 'free', is in AVOID, so it is allowed once per text with no other AVOID word.
"""
import re

NEVER = [
    # health and disease claims
    r'cure[sd]?', r'curing', r'heals? (?:your )?(?:diabetes|cancer|thyroid|autism|anxiety|depression)', r'eliminate (?:disease|diabetes|cancer)',
    r'diagnos\w+', r'prescri\w+', r'therap(?:y|ist|ies|eutic)', r'treat(?:s|ed|ing|ment)? (?:of )?(?:anxiety|depression|trauma|adhd|disease|illness)',
    r'weight loss', r'lose (?:\d+ )?(?:pounds|lbs|weight|fat)', r'burn fat', r'miracle', r'reverses? aging',
    # money and income claims
    r'passive income', r'extra income', r'make money', r'earn\w*', r'get paid', r'fast cash', r'financial freedom', r'six figures?', r'seven figures?',
    r'\d+k a month', r'guaranteed (?:results|income|profit|clients)', r'guarantee[sd]?', r'money back', r'work from home', r'mlm', r'multi-level',
    # risk, hype and urgency spam
    r'risk[- ]free', r'zero risk', r'no risk', r'100%', r'no strings', r'no catch', r'fool-?proof', r'fail[- ]?proof', r'once in a lifetime',
    r'act now', r'apply now', r'call now', r'click here', r'click now', r'order now', r'buy now', r'hurry', r'urgent', r'last chance', r'limited time',
    r'while (?:supplies|stocks) last', r"don'?t delete", r'you have been selected', r'congratulations', r'winner', r'unlimited', r'viagra',
]
AVOID = [
    r'free', r'bonus', r'deal', r'discount', r'exclusive', r'special', r'instant(?:ly)?', r'immediately', r'trial', r'gift', r'buy', r'order',
    r'subscribe', r'money', r'profit', r'opportunity', r'income', r'cheap', r'prize', r'refund', r'amazing', r'incredible', r'ultimate',
    r'important information', r'please read', r'best price', r'clearance',
]
NICHE = [
    r'copywrit\w+', r'content creator', r'i help (?:busy )?coaches', r'i came across', r'i noticed', r'hope this finds you', r'quick question',
    r'loved your post', r'following up', r'just checking', r'circling back', r'bumping', r'leverag\w+', r'streamlin\w+', r'elevat\w+', r'unlock\w*',
    r'game[- ]?chang\w+', r'journey', r'delve', r'synerg\w+', r'\bhacks?\b', r'\bguru\b', r'i wrote you a post', r'my system writes posts',
    r'interested\?', r'want to see it\?', r'would you be open',
]

def _find(patterns, text):
    hits = []
    for p in patterns:
        m = re.search(r'(?<![A-Za-z])(?:' + p + r')(?![A-Za-z])', text, re.I)
        if m:
            hits.append(m.group(0).lower())
    return hits

def check(text):
    """Returns (ok, {'never': [...], 'avoid': [...], 'niche': [...]}). Not ok on any NEVER or NICHE hit, or on 2+ AVOID hits."""
    t = text or ''
    n, a, c = _find(NEVER, t), _find(AVOID, t), _find(NICHE, t)
    return (not n and not c and len(a) < 2), {'never': n, 'avoid': a, 'niche': c}

if __name__ == '__main__':
    import sys, json
    print(check(sys.stdin.read()))
