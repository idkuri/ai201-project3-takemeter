/** Paste in browser console on old.reddit.com, save output to data/reddit_pool/fresh_analysis.json */

(async () => {
  const urls = [
    "https://old.reddit.com/r/leagueoflegends/search.json?q=patch+winrate&restrict_sr=1&sort=new&limit=100",
    "https://old.reddit.com/r/leagueoflegends/search.json?q=mechanics+ability&restrict_sr=1&sort=new&limit=100",
    "https://old.reddit.com/r/leagueoflegends/search.json?q=pro+play+meta&restrict_sr=1&sort=new&limit=100",
    "https://old.reddit.com/r/leagueoflegends/new.json?limit=100",
    "https://old.reddit.com/r/leagueoflegends/hot.json?limit=100",
  ];
  const analysisRe = /patch \d+\.\d|winrate|pickrate|dpm\.lol|op\.gg|u\.gg|mechanic|cooldown|ability|passive|interaction|rework|pro play|breakdown|compared to|calculated|patch notes|https:\/\//i;
  const questionRe = /^(how do i|how to|any suggestions|need help|what champ|which champ|advice on|beginner|new player)/i;
  const hotRe = /unpopular opinion|should be brought back|i miss |overtuned|this game sucks|elo hell/i;
  const promoRe = /coaching|buy me a coffee|sign ups|my website|discord\.gg/i;
  const seen = new Set();
  const out = [];

  for (const url of urls) {
    const d = await fetch(url).then((r) => r.json());
    for (const c of d.data.children) {
      const p = c.data;
      if (seen.has(p.id)) continue;
      seen.add(p.id);
      const selftext = (p.selftext || "").replace(/&amp;/g, "&").trim();
      if (!p.is_self || p.over_18 || selftext.length < 80) continue;
      const text = (p.title + " " + selftext).slice(0, 1500);
      if (promoRe.test(text.slice(0, 400))) continue;
      if (questionRe.test(text.slice(0, 120)) && !analysisRe.test(text)) continue;
      if (hotRe.test(text.slice(0, 400)) && !analysisRe.test(text.slice(0, 600))) continue;
      let score = 0;
      if (analysisRe.test(text)) score += 2;
      if (/\b(because|therefore|for example|means that)\b/i.test(text)) score += 1;
      if (text.length > 400) score += 1;
      if (/Educational|Esports|News/i.test(p.link_flair_text || "")) score += 1;
      if (questionRe.test(text.slice(0, 120))) score -= 2;
      if (score < 2) continue;
      out.push({
        reddit_id: p.id,
        title: p.title.trim(),
        selftext,
        url: "https://old.reddit.com" + p.permalink,
        source_url: "https://old.reddit.com" + p.permalink,
        flair: p.link_flair_text || "",
        score: p.score,
        analysis_score: score,
      });
    }
  }
  out.sort((a, b) => b.analysis_score - a.analysis_score || b.selftext.length - a.selftext.length);
  const payload = JSON.stringify({ count: out.length, posts: out.slice(0, 80) }, null, 2);
  console.log(payload);
  return payload;
})();
