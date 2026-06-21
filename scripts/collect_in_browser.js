/**
 * Run this in the Cursor browser on old.reddit.com/r/leagueoflegends
 * (DevTools console or via browser CDP Runtime.evaluate).
 *
 * Fetches 200+ self-posts with permalinks from Reddit's JSON API.
 * Copy the printed JSON to data/reddit_pool/collection.json, then:
 *   python scripts/rebalance_dataset.py import-pool data/reddit_pool/collection.json
 *   python scripts/rebalance_dataset.py status
 */
(async () => {
  const subs = ["new", "hot", "top"];
  const out = [];
  const seen = new Set();

  for (const sort of subs) {
    let after = null;
    for (let page = 0; page < 4; page++) {
      let url = `https://old.reddit.com/r/leagueoflegends/${sort}.json?limit=100`;
      if (after) url += `&after=${after}`;
      const d = await fetch(url).then((r) => r.json());

      for (const c of d.data.children) {
        const p = c.data;
        if (seen.has(p.id)) continue;
        seen.add(p.id);

        const selftext = (p.selftext || "")
          .replace(/&amp;/g, "&")
          .replace(/&gt;/g, ">")
          .replace(/&lt;/g, "<")
          .replace(/&#x200B;/g, "")
          .trim();

        if (!p.is_self || p.over_18 || p.removed_by_category) continue;
        if (selftext.length < 40) continue;
        if (/automoderator/i.test(selftext)) continue;

        out.push({
          reddit_id: p.id,
          title: p.title.trim(),
          selftext,
          source_url: `https://old.reddit.com${p.permalink}`,
          flair: p.link_flair_text || "",
          author: p.author,
          score: p.score,
          sort,
        });

        if (out.length >= 250) break;
      }

      after = d.data.after;
      if (!after || out.length >= 250) break;
    }
    if (out.length >= 250) break;
  }

  const payload = JSON.stringify({ count: out.length, posts: out }, null, 2);
  console.log(payload);
  return payload;
})();
