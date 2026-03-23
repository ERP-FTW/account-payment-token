# CardPointe Base Endpoints

The API base URL is configured in **CardPointe API Base URL** (`cardpointe_api_base`). Endpoints
below are appended to that base URL.

## Endpoints used by this module

- **Test Connection**: `cardpointe_test_endpoint` (preferred) or `ENDPOINT_TEST_CONNECTION`
  - Set **Test Connection Endpoint** on the provider to the relative endpoint from Gateway API docs.
  - TODO: set `ENDPOINT_TEST_CONNECTION` per Gateway API docs.
  - See: https://developer.fiserv.com/product/CardPointe/docs/?path=docs/APIs/CardPointeGatewayAPI.md

## Notes

Do not invent endpoint paths or payload fields; confirm in docs.
