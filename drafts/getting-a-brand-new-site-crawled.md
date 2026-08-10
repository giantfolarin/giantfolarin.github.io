A domain registered this morning has no reputation, no inbound links, and no
history. Google has no reason to visit it and no way to know it exists. Getting
that first crawl is a different problem from ranking, and it's worth separating
the two because the advice for each is almost opposite.

## Discovery is the only problem at the start

Ranking advice assumes Google already knows about your page and is deciding
where to place it. None of that applies yet. Before any of it matters, a
crawler has to fetch the URL once.

There are exactly four ways that happens. Google follows a link to it from a
page it already crawls. It reads the URL in a sitemap it already knows about.
Somebody submits it through Search Console. Or the Indexing API tells it
directly. On a new site the first two don't exist yet, which leaves the last
two.

## What I did, in order

Verified the property in Search Console first, because every other step depends
on it. Ownership is checked on every API call, and without it the Indexing API
returns a flat 403 no matter how well-formed the request is.

Then a sitemap with real `lastmod` values, linked from `robots.txt`. This costs
nothing and it's the cheapest discovery signal available. Two URLs is a
perfectly respectable sitemap.

Then the Indexing API for both pages. Accepted immediately, which means a fetch
is scheduled — not that anything is indexed.

Then IndexNow, which is one request that reaches Bing, Yandex, Naver, Seznam
and Yep at once. Bing in particular indexes new sites considerably faster than
Google does, and Bing's index feeds several other things, so it's free coverage
for about a minute of setup.

## What the status actually means

The URL Inspection API gives you a `coverageState` string, and the wording is
precise in a way that's easy to skim past.

- **URL is unknown to Google** — never crawled. Discovery hasn't happened yet.
- **Discovered — currently not indexed** — Google knows the URL exists but
  hasn't spent the crawl budget on it. This is about your site's perceived
  value, not about the page.
- **Crawled — currently not indexed** — it was fetched and passed over. That is
  a quality judgement and resubmitting will not change it.
- **Submitted and indexed** — done.

The move from the first to the second is discovery. The move from the third to
the fourth is everything else, and no tool can do it for you.

## The part I keep having to accept

Nothing forces indexing. The APIs schedule a crawl; they don't buy a slot in
the index. Every service selling guaranteed indexing is selling the crawl
trigger and hoping you conflate the two.

What's actually within your control is narrow but real: make the page reachable,
make it unambiguous which URL is canonical, remove anything that says not to
index it, give it enough substance to be worth a slot, and link to it from
somewhere that already gets crawled. Then submit it and wait. On a new domain
the waiting is measured in days, and there is no version of this where impatience
helps.
