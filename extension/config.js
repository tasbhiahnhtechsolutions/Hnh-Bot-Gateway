// Gateway endpoint configuration (DEV defaults).
//
// `node build.mjs --env <dev|staging|prod>` writes a generated copy of this
// file into dist/<env>/ with that environment's GATEWAY_BASE_URL, and the
// matching manifest. Load dist/<env>/ in Chrome, not this folder.
// Never put secrets here: the API key is entered by the user in the popup.
self.WA_GATEWAY_CONFIG = {
  env: 'dev',
  GATEWAY_BASE_URL: 'http://localhost:4149',
};
