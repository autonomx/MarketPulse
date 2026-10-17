/**
 * The product name shown in user-visible text (MarketPulse S-3).
 *
 * Fixed at build time from ``VITE_PRODUCT_NAME`` so module-level strings see it
 * too; Omnix builds leave it unset and show "Omnix". Internal identifiers
 * (storage keys, window names, cookies) never use it.
 */
export const PRODUCT_NAME: string = import.meta.env.VITE_PRODUCT_NAME?.trim() || 'Omnix';

/** The product name with its indefinite article ("an Omnix", "a MarketPulse"). */
export const PRODUCT_NAME_WITH_ARTICLE = `${/^[aeiou]/i.test(PRODUCT_NAME) ? 'an' : 'a'} ${PRODUCT_NAME}`;
