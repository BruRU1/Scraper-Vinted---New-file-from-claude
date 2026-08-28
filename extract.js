// extract.js
// v2 — rewritten using real Vinted markup (confirmed via live DOM inspection),
// not guessed selectors. Vinted's CSS classnames are randomly hashed per build
// (e.g. "ItemBoxPricing-module-scss-module__xkFchG__new-item-box__title"), but
// data-testid attributes reliably END with a stable, semantic suffix
// (e.g. "--price-text", "--description-title"). We match on that suffix using
// CSS attribute selectors ($=), which survive Vinted's hash changes.

(() => {
  // Card container: match by class SUFFIX (stable) rather than the
  // randomly-hashed prefix, using a "contains" selector.
  const CARD_SELECTOR = '[class*="new-item-box__container"]';

  function queryAll(root, selector) {
    try {
      return Array.from(root.querySelectorAll(selector));
    } catch (e) {
      return [];
    }
  }

  function queryOne(root, selector) {
    try {
      return root.querySelector(selector);
    } catch (e) {
      return null;
    }
  }

  function cleanText(el) {
    if (!el) return '';
    return el.textContent.replace(/\s+/g, ' ').trim();
  }

  // Fixed: Vinted's actual format is "25.00 £" (number THEN symbol),
  // not "£25.00". Original regex only matched symbol-first and silently
  // failed on every real listing.
  function extractPrice(rawText) {
    if (!rawText) return { price: '', currency: '' };
    let match = rawText.match(/(\d+(?:[.,]\d{1,2})?)\s?([£$€])/);
    if (match) {
      const symbolMap = { '£': 'GBP', '$': 'USD', '€': 'EUR' };
      return { price: match[1].replace(',', '.'), currency: symbolMap[match[2]] || '' };
    }
    match = rawText.match(/([£$€])\s?(\d+(?:[.,]\d{1,2})?)/);
    if (match) {
      const symbolMap = { '£': 'GBP', '$': 'USD', '€': 'EUR' };
      return { price: match[2].replace(',', '.'), currency: symbolMap[match[1]] || '' };
    }
    return { price: '', currency: '' };
  }

  function absoluteUrl(possiblyRelative) {
    if (!possiblyRelative) return '';
    try {
      return new URL(possiblyRelative, window.location.origin).href;
    } catch (e) {
      return possiblyRelative;
    }
  }

  // The overlay link's title attribute (and the img's alt attribute) contain
  // a full structured string, e.g.:
  // "Renault f1 gilet..., brand: Renault, condition: Very good, size: S,
  //  25.00 £, 26.95 £ includes Buyer Protection"
  function parseVintedTitleAttribute(text) {
    if (!text) return null;

    let m = text.match(
      /^(.*?),\s*brand:\s*(.*?),\s*condition:\s*(.*?),\s*size:\s*(.*?),\s*(\d+(?:[.,]\d{1,2})?\s?[£$€])/i
    );
    if (m) {
      return { title: m[1].trim(), brand: m[2].trim(), condition: m[3].trim(), size: m[4].trim(), priceRaw: m[5].trim() };
    }

    m = text.match(
      /^(.*?),\s*condition:\s*(.*?),\s*size:\s*(.*?),\s*(\d+(?:[.,]\d{1,2})?\s?[£$€])/i
    );
    if (m) {
      return { title: m[1].trim(), brand: '', condition: m[2].trim(), size: m[3].trim(), priceRaw: m[4].trim() };
    }

    m = text.match(
      /^(.*?),\s*condition:\s*(.*?),\s*(\d+(?:[.,]\d{1,2})?\s?[£$€])/i
    );
    if (m) {
      return { title: m[1].trim(), brand: '', condition: m[2].trim(), size: '', priceRaw: m[3].trim() };
    }

    return null;
  }

  function extractListingFromCard(card) {
    const urlEl = queryOne(card, 'a[data-testid$="--overlay-link"]') || queryOne(card, 'a');
    const rawUrl = urlEl ? (urlEl.getAttribute('href') || '') : '';
    const attributeText = urlEl ? urlEl.getAttribute('title') : '';
    const parsed = parseVintedTitleAttribute(attributeText);

    const imageEl = queryOne(card, 'img[data-testid$="--image--img"]') || queryOne(card, 'img');
    const rawImage = imageEl ? (imageEl.getAttribute('src') || imageEl.getAttribute('data-src') || '') : '';
    const imageAlt = imageEl ? (imageEl.getAttribute('alt') || '') : '';

    let title, brand, condition, size, price, currency;

    if (parsed) {
      title = parsed.title;
      brand = parsed.brand;
      condition = parsed.condition;
      size = parsed.size;
      ({ price, currency } = extractPrice(parsed.priceRaw));
    } else {
      const titleOrBrandEl = queryOne(card, '[data-testid$="--description-title"]');
      const subtitleEl = queryOne(card, '[data-testid$="--description-subtitle"]');
      const priceEl = queryOne(card, '[data-testid$="--price-text"]');

      const altParsed = parseVintedTitleAttribute(imageAlt);
      if (altParsed) {
        title = altParsed.title;
        brand = altParsed.brand;
        condition = altParsed.condition;
        size = altParsed.size;
        ({ price, currency } = extractPrice(altParsed.priceRaw));
      } else {
        title = cleanText(titleOrBrandEl);
        brand = cleanText(titleOrBrandEl);
        const subtitleText = cleanText(subtitleEl);
        if (subtitleText.includes('\u00b7')) {
          const parts = subtitleText.split('\u00b7').map(s => s.trim());
          size = parts[0] || '';
          condition = parts[1] || '';
        } else {
          size = '';
          condition = subtitleText;
        }
        ({ price, currency } = extractPrice(cleanText(priceEl)));
      }
    }

    return {
      title: title || '',
      price: price || '',
      currency: currency || '',
      brand: brand || '',
      category: '',
      size: size || '',
      condition: condition || '',
      url: absoluteUrl(rawUrl),
      imageUrl: absoluteUrl(rawImage)
    };
  }

  let cards = queryAll(document, CARD_SELECTOR);

  if (cards.length === 0) {
    const fallbackSelectors = [
      '[data-testid="grid-item"]',
      '.feed-grid__item',
      'div[data-testid^="product-item"]',
      'a.new-item-box__container'
    ];
    for (const sel of fallbackSelectors) {
      cards = queryAll(document, sel);
      if (cards.length > 0) break;
    }
  }

  if (!cards || cards.length === 0) {
    return { success: false, error: 'No listing cards found — page structure may have changed.', listings: [] };
  }

  const listings = [];
  for (const card of cards) {
    const listing = extractListingFromCard(card);
    if (!listing.url && !listing.title) continue;
    listings.push(listing);
  }

  return { success: true, error: '', listings };
})();
