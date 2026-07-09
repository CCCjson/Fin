# capitaliq.com 逆向接口说明

- 侦查页面：https://www.capitaliq.com/
- 侦查时间：2026-07-03T15:34:47
- 端点数量：7

> 由 scrape 侦查时自动产出；scrape 读同目录 .json 复用取数；本文件供人工查阅/参考。

## 1. en

- URL：`https://cdn.cookielaw.org/consent/78c55ba0-b374-42fa-9ec2-995ab5080989/019ac54b-ac95-7380-b7c8-7db776bfa997/en.json`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`

## 2. otFlat

- URL：`https://cdn.cookielaw.org/scripttemplates/202606.1.0/assets/otFlat.json`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`

## 3. 78c55ba0-b374-42fa-9ec2-995ab5080989

- URL：`https://cdn.cookielaw.org/consent/78c55ba0-b374-42fa-9ec2-995ab5080989/78c55ba0-b374-42fa-9ec2-995ab5080989.json`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`

## 4. location

- URL：`https://geolocation.onetrust.com/cookieconsentpub/v1/geo/location`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile`

## 5. openid-configuration

- URL：`https://secure.signin.spglobal.com/oauth2/spglobal/.well-known/openid-configuration`
- 方法：GET　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile, x-okta-user-agent-extended`
- 字段样例：issuer, authorization_endpoint, token_endpoint, userinfo_endpoint, registration_endpoint, jwks_uri, response_types_supported, response_modes_supported, grant_types_supported, subject_types_supported, id_token_signing_alg_values_supported, id_token_encryption_alg_values_supported, id_token_encryption_enc_values_supported, scopes_supported, token_endpoint_auth_methods_supported, claims_supported, code_challenge_methods_supported, introspection_endpoint, introspection_endpoint_auth_methods_supported, revocation_endpoint

## 6. interact

- URL：`https://secure.signin.spglobal.com/oauth2/spglobal/v1/interact`
- 方法：POST　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile, x-okta-user-agent-extended`

## 7. introspect

- URL：`https://secure.signin.spglobal.com/idp/idx/introspect`
- 方法：POST　状态：200　JSON：True
- Gate 头：`sec-ch-ua-platform, referer, sec-ch-ua, sec-ch-ua-mobile, x-okta-user-agent-extended`
