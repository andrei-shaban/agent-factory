# LDAP-федерация для Keycloak

Production Blueprint v5.1 — ВМ №1

Шаблон конфигурации **не импортируется автоматически**. После импорта репрезентации реалма настройте федерацию вручную через UI Keycloak.

## Шаги

1. Войдите в администраторскую консоль Keycloak (https://factory.corp.example.com/auth/admin).
2. Перейдите в **Realm Settings → User Federation → Add provider**.
3. Выберите тип провайдера: **ldap**.
4. Введите значения из шаблона ниже.
5. Сохраните и нажмите **Synchronize all users** для первичного импорта.

## Шаблон конфигурации

### Основные параметры

| Параметр | Значение |
|---|---|
| **Console display name** | AD Corporation |
| **Priority** | `0` |
| **Sync registrations** | `false` (READ_ONLY) |
| **Edit mode** | `READ_ONLY` |
| **Vendor** | `Active Directory` |
| **Username LDAP attribute** | `sAMAccountName` |
| **RDN LDAP attribute** | `cn` |
| **UUID LDAP attribute** | `objectGUID` |
| **User object classes** | `person, organizationalPerson, user` |
| **Connection URL** | `ldaps://ad.corp.example.com:636` |
| **Users DN** | `OU=Users,DC=corp,DC=example,DC=com` |
| **Bind DN** | `CN=svc-keycloak,OU=ServiceAccounts,DC=corp,DC=example,DC=com` |
| **Bind Credential** | `<Пароль сервисной учётной записи>` |
| **Custom user search filter** | `(memberOf=CN=factory-users,OU=Groups,DC=corp,DC=example,DC=com)` |
| **Search scope** | `Subtree` |
| **Validate password policy** | `false` |
| **Use truststore SPI** | `ldapsOnly` |
| **Connection timeout** | `5000` |
| **Read timeout** | `10000` |
| **Pagination** | `true` |
| **Start TLS** | `false` |

### Мапперы групп

Создайте маппер для синхронизации членства в группах:

1. В настройках провайдера нажмите **Add mapper**.
2. Выберите тип: **group-ldap-mapper**.
3. Введите значения:

| Параметр | Значение |
|---|---|
| **Name** | `ldap-groups-mapper` |
| **LDAP Groups DN** | `OU=Groups,DC=corp,DC=example,DC=com` |
| **Group Name LDAP Attribute** | `cn` |
| **Group Object Classes** | `group` |
| **Preserve Group Inheritance** | `true` |
| **Ignore Missing Groups** | `true` |
| **Membership LDAP Attribute** | `member` |
| **Membership Attribute Type** | `DN` |
| **Membership User LDAP Attribute** | `objectGUID` |
| **LDAP Filter** | `(memberOf=CN=factory-users,OU=Groups,DC=corp,DC=example,DC=com)` |
| **Mode** | `READ_ONLY` |
| **User Roles Retrieve Strategy** | `GET_GROUPS_FROM_USER_MEMBEROF_ATTRIBUTE` |
| **Member-Of LDAP Attribute** | `memberOf` |

4. Сохраните маппер и нажмите **Sync groups from LDAP**.

## Проверка

После настройки:

1. В разделе **Users** появятся пользователи из домена.
2. При входе пользователя через корпоративный домен аутентификация выполняется через Keycloak.
3. Членство в группах доступно в клейме `groups` ID-токена и пробрасывается в `X-Forwarded-Groups` через nginx.

## Примечания

- **Доверие сертификату**: `useTruststoreSpi: ldapsOnly` означает, что Keycloak будет проверять сертификат сервера домена через встроенный трастовый стор. Корпоративный Root CA уже вшит в Docker-образ Keycloak.
- **READ_ONLY режим**: пользователи, созданные в домене, импортируются в Keycloak, но пароли и атрибуты не изменяются. Управление пользователями — на стороне домена.
- **Фильтр пользователей**: фильтр `memberOf=CN=factory-users,...` ограничивает импорт только членами группы `factory-users`. Пользователи вне этой группы не будут видны в фабрике.