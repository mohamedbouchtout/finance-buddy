# Launch Checklist — Finance Buddy

This is the actionable, step-by-step path from "code on disk" to "first paying customer."

## ☑ 1. Domain & Branding (1 hour, ~$12)
- [ ] Buy domain — recommended: `financebuddy.com` (Namecheap, Cloudflare, Porkbun)
- [ ] Create a logo (use Bing Image Creator / Midjourney with prompt: "minimalist analytics dashboard logo, blue and slate, modern")
- [ ] Generate favicon at https://favicon.io
- [ ] Update `templates/base.html` `<title>` and `<meta description>` if needed

## ☑ 2. Stripe Setup (30 min, $0)
- [ ] Create Stripe account at https://stripe.com (US business or sole prop is fine)
- [ ] Switch to **Test mode** first
- [ ] Products → Add product: "Finance Buddy Pro"
  - [ ] Recurring price 1: $9.00 / monthly → copy `price_xxxx` → set as `STRIPE_PRICE_MONTHLY`
  - [ ] Recurring price 2: $79.00 / yearly → copy `price_xxxx` → set as `STRIPE_PRICE_YEARLY`
- [ ] API keys → copy **Secret key** → `STRIPE_SECRET_KEY`
- [ ] Developers → Webhooks → Add endpoint:
  - URL: `https://yourdomain.com/webhooks/stripe`
  - Events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`
  - Copy signing secret → `STRIPE_WEBHOOK_SECRET`
- [ ] Tax & legal: enable Stripe Tax for automated sales tax. Set up your business address.
- [ ] Test with card `4242 4242 4242 4242`, any future expiry, any CVC
- [ ] Activate live mode → re-do prices + webhook for live → update env vars

## ☑ 3. Email (30 min, free tier)
Option A: **Resend** (recommended — easy)
- [ ] Sign up at https://resend.com
- [ ] Add and verify your domain (DKIM + SPF records)
- [ ] Use their SMTP relay: `SMTP_HOST=smtp.resend.com`, `SMTP_USER=resend`, `SMTP_PASS=<api-key>`

Option B: **SendGrid free tier** (100 emails/day)
- [ ] Same idea — verify sender, then SMTP creds

## ☑ 4. Hosting (15 min, $5/mo)

### Recommended: Fly.io (free tier covers this app)
```powershell
fly launch --no-deploy        # generates fly.toml
# Edit fly.toml: app name, region, set [env] APP_BASE_URL
fly secrets set APP_SECRET=<random-long-string>
fly secrets set STRIPE_SECRET_KEY=sk_live_...
fly secrets set STRIPE_WEBHOOK_SECRET=whsec_...
fly secrets set STRIPE_PRICE_MONTHLY=price_...
fly secrets set STRIPE_PRICE_YEARLY=price_...
fly secrets set SMTP_HOST=... SMTP_USER=... SMTP_PASS=... EMAIL_FROM=hello@yourdomain.com
fly secrets set ADMIN_EMAIL=you@yourdomain.com
fly volumes create data --size 1            # persist SQLite
# Add a [mounts] section in fly.toml pointing /app/data to that volume
fly deploy
fly certs add financebuddy.com
fly certs add www.financebuddy.com
```

Alternatives: Railway, Render, DigitalOcean App Platform (all work with the Dockerfile).

## ☑ 5. Pre-launch verification
- [ ] Visit https://yourdomain.com — landing loads
- [ ] Run an analysis with sample portfolio → results render with real prices
- [ ] Sign in with magic link → arrives in inbox
- [ ] Upgrade to Pro with **real test card** in Stripe test mode → webhook fires → dashboard shows Pro
- [ ] Cancel via billing portal → webhook downgrades to free
- [ ] Export PDF → opens cleanly
- [ ] Import a CSV → positions parse correctly
- [ ] Hit `/admin` while logged in as `ADMIN_EMAIL` → see event counts
- [ ] Switch Stripe to **Live mode** and repeat smoke test with a $1 promo or yourself

## ☑ 6. Customer acquisition — first 20 customers

**Channel 1: Reddit (highest signal)**
- r/investing (3M members) — post a detailed comparison: "I built a tool that shows your true Mag 7 exposure including ETF look-through. Mine was 47%. Here's why this matters." Include screenshots and the tool link.
- r/Bogleheads — angle: "Are 3-fund portfolios really diversified? I built a look-through analyzer to find out."
- r/Fire — angle on retirement risk concentration
- r/wallstreetbets — meme-able share cards
- Rules: lead with value, no shilling. Be a member first.

**Channel 2: Twitter/X**
- Post your own portfolio score with the shareable card
- Quote-tweet investing influencers when relevant
- Hashtags: #investing #bogleheads #portfolio

**Channel 3: Substack/Newsletter outreach**
- Email 10 finance newsletter writers (The Daily Upside, Finimize, Compound) offering them free Pro + a custom analysis of their portfolio for a mention.

**Channel 4: SEO content**
- Blog post: "How concentrated is the S&P 500 in 2026?"
- Blog post: "What is HHI and why your portfolio's matters"
- Blog post: "VOO vs VTI vs VT — true diversification compared"

**Channel 5: Product Hunt** (after 100 free users)
- Launch on a Tuesday or Wednesday for max visibility.

## ☑ 7. Metrics to watch (week 1)
| Metric | Target |
|---|---|
| Unique landing visitors | 500 |
| Analyses run | 100 |
| Magic-link signups | 30 |
| Pro conversions | 3–5 |
| MRR | $27–$45 |

## ☑ 8. Iteration plan (week 2+)
- Add "compare two portfolios" feature
- Add "what if I rebalance to X" simulator (gated)
- Refresh ETF holdings quarterly (set calendar reminder)
- Add more brokers' CSV formats based on user feedback
- Twitter/Reddit feedback channel: monitor weekly

## ☑ 9. Support
- [ ] Set up `hello@financebuddy.com` forwarding to your inbox
- [ ] Canned reply templates for: password reset (we don't have one — explain magic link), cancellation, refund, ETF data is outdated, can you add X ticker

## ☑ 10. Legal cover-your-ass
- [ ] Footer disclaimer (already in place)
- [ ] Terms of Service & Privacy Policy — generate from https://termly.io free tier
- [x] **Pro liability waiver** — `/waiver` is enforced before `/bot/app` and `/bot/live-config`. Acceptance (email, version, IP, timestamp) is stored in `users.waiver_*` and bundled into every download as `waiver_acceptance.json` + `WAIVER.txt`. Review the wording in `app/legal/waiver.txt` with counsel before launch.
- [ ] **Do not** make personalized recommendations. Keep all copy educational.
- [ ] No tax advice, no "buy this/sell this" language.

## ☑ 11. Pro desktop app — pre-launch smoke test
- [ ] Log in as a Pro user, accept the waiver at `/waiver`, download from `/bot/app`.
- [ ] Unzip the bundle on a clean Windows VM. Double-click `run.bat` — first run installs deps, app launches.
- [ ] Confirm the **Signal Matrix** tab populates after the first scan (uses live yfinance).
- [ ] Confirm **Start / Stop** buttons work and the **Activity Log** shows scan + license messages.
- [ ] Move the **Risk per trade** and **AI confidence** sliders, hit **Save config**, restart — values persisted.
- [ ] Cancel the user's subscription in Stripe — confirm the bot's next license heartbeat fails and the app refuses to open new positions.
- [ ] Optional: `pip install ib_insync`, run IB Gateway in paper mode, switch the app to **Live (IB)**, click **Connect IB…** and verify a small paper order is placed.
