// extract.js
// Ported directly from the Chrome extension's selectors.js + content.js.
// Same selectors, same fallback logic, same Vinted title-attribute parser.
// Runs inside the page via Playwright's page.evaluate().

(() => {
  const SELECTORS = {
    listingCard: [
      '[data-testid="grid-item"]',
      '.feed-grid__item',
      'div[data-testid^="product-item"]',
      'a.new-item-box__container'
    ],
    title: [
      '[data-testid="product-item-title"]',
      '.new-item-box__title',
      'p.web_ui__Text__title',
      'h3'
    ],
    price: [
      '[data-testid="product-item-price"]',
      '.new-item-box__price',
      'p.web_ui__Text__subtitle',
      'span[data-testid$="--price-text"]'
    ],
    brand: [
      '[data-testid="product-item-brand"]',
      '.new-item-box__brand'
    ],
    size: [
      '[data-testid="product-item-size"]',
      '.new-item-box__size'
    ],
    condition: [
      '[data-testid="product-item-condition"]',
      '.new-item-box__condition'
    ],
    image: [
      'img[data-testid="product-item-photo"]',
      'img.web_ui__Image__content',
      'img'
    ],
    url: [
      'a[data-testid="product-item-id--overlay-link"]',
      'a.new-item-box__overlay',
      'a'
    ],
    category: []
  };

  function queryFirstMatch(root, selectorList, all = false) {
    if (!selectorList) return all ? [] : null;
    for (const sel of selectorList) {
      try {
        if (all) {
          const found = root.querySelectorAll(sel);
          if (found && found.length > 0) return Array.from(found);
        } else {
          const found = root.querySelector(sel);
          if (found) return found;
        }
      } catch (e) {
        continue;
      }
    }
    return all ? [] : null;
  }

  function cleanText(el) {
    if (!el) return '';
    return el.textContent.replace(/\s+/g, ' ').trim();
  }

  function extractPrice(rawText) {
    if (!rawText) return { price: '', currency: '' };
    const match = rawText.match(/([£$€])?\s?(\d+(?:[.,]\d{1,2})?)\s?(GBP|USD|EUR)?/i);
    if (!match) return { price: '', currency: '' };
    const symbolMap = { '£': 'GBP', '$': 'USD', '€': 'EUR' };
    const currency = (match[1] && symbolMap[match[1]]) || match[3] || '';
    const price = match[2] ? match[2].replace(',', '.') : '';
    return { price, currency };
  }

  function absoluteUrl(possiblyRelative) {
    if (!possiblyRelative) return '';
    try {
      return new URL(possiblyRelative, window.location.origin).href;
    } catch (e) {
      return possiblyRelative;
    }
  }

  function parseVintedTitleAttribute(text) {
    if (!text) return null;

    let m = text.match(
      /^(.*?),\s*brand:\s*(.*?),\s*condition:\s*(.*?),\s*size:\s*(.*?),\s*([£$€]\s?[\d.,]+)/i
    );
    if (m) {
      return {
        title: m[1].trim(),
        brand: m[2].trim(),
        condition: m[3].trim(),
        size: m[4].trim(),
        priceRaw: m[5].trim()
      };
    }

    m = text.match(
      /^(.*?),\s*condition:\s*(.*?),\s*size:\s*(.*?),\s*([£$€]\s?[\d.,]+)/i
    );
    if (m) {
      return {
        title: m[1].trim(),
        brand: '',
        condition: m[2].trim(),
        size: m[3].trim(),
        priceRaw: m[4].trim()
      };
    }

    return null;
  }

  function extractListingFromCard(card) {
    let urlEl = queryFirstMatch(card, SELECTORS.url);
    if (!urlEl && card.tagName === 'A') urlEl = card;
    const rawUrl = urlEl ? (urlEl.getAttribute('href') || '') : '';

    const attributeText = urlEl ? urlEl.getAttribute('title') : '';
    const parsed = parseVintedTitleAttribute(attributeText);

    const imageEl = queryFirstMatch(card, SELECTORS.image);
    const rawImage = imageEl
      ? (imageEl.getAttribute('src') || imageEl.getAttribute('data-src') || '')
      : '';

    let title, brand, condition, size, price, currency;

    if (parsed) {
      title = parsed.title;
      brand = parsed.brand;
      condition = parsed.condition;
      size = parsed.size;
      ({ price, currency } = extractPrice(parsed.priceRaw));
    } else {
      const titleEl = queryFirstMatch(card, SELECTORS.title);
      const priceEl = queryFirstMatch(card, SELECTORS.price);
      const brandEl = queryFirstMatch(card, SELECTORS.brand);
      const sizeEl = queryFirstMatch(card, SELECTORS.size);
      const conditionEl = queryFirstMatch(card, SELECTORS.condition);

      title = cleanText(titleEl);
      brand = cleanText(brandEl);
      condition = cleanText(conditionEl);
      size = cleanText(sizeEl);
      ({ price, currency } = extractPrice(cleanText(priceEl)));
    }

    const categoryEl = queryFirstMatch(card, SELECTORS.category);
    const category = cleanText(categoryEl);

    return {
      title,
      price,
      currency,
      brand,
      category,
      size,
      condition,
      url: absoluteUrl(rawUrl),
      imageUrl: absoluteUrl(rawImage)
    };
  }

  const cards = queryFirstMatch(document, SELECTORS.listingCard, true);
  if (!cards || cards.length === 0) {
    return { success: false, error: 'No listing cards found — selectors may be outdated.', listings: [] };
  }

  const listings = [];
  for (const card of cards) {
    const listing = extractListingFromCard(card);
    if (!listing.url && !listing.title) continue;
    listings.push(listing);
  }

  return { success: true, error: '', listings };
})();
