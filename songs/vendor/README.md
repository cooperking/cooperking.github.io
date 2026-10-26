# Third-party code used by the admin pages

- `heic2any.min.js`: heic2any 0.0.4 (npm, MIT licence, https://github.com/alexcorvi/heic2any), unchanged. Turns an
  iPhone's HEIC/HEIF photo into a JPEG in the browser, for the admin pages' picture pickers in browsers that can't open
  HEIC (Chrome, Firefox). Loaded only when such a photo is chosen (`admin-cards.js`, `fileImage`).
