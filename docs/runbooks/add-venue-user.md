# Add a venue user (pilot)

How a person gets access to a venue in the venue app (ADR-0019). In-app invites come later. Until
then an operator does three steps by hand. All three are needed: a user missing from any one of
them gets a 403, never someone else's data.

## Symptoms this fixes

- A user signs in and the app shows no venues. `/me` returns `venues: []`.
- `/me` returns 403 `not_a_user`: the stamp has no active user for this sign-in.
- `/me` returns 403 `tenant_not_on_stamp`: the tenant attribute is missing, wrong, or points to a
  tenant on another stamp.

## Steps

1. **Entra External ID: the account.** Create or invite the user in the External ID tenant. Copy
   their **Object ID** (`oid`).
2. **Entra External ID: the tenant attribute.** Set the user's tenant custom attribute to our tenant
   ID (UUID) for their organisation. It is the `tenant_id` returned when the venue was onboarded
   (`POST /venues`), also in the catalog's `venue_directory.tenant_id`.
   - The attribute must be emitted in **access tokens** for the Platform API under the claim name in
     `TENANT_CLAIM` (default `va_tenant_id`).
   - The attribute must **not** be collected in a sign-up flow or be editable in profile edit.
     Only operators set it.
   - Not yet verified on a real tenant (open-questions.md). If it doesn't appear, the fallback is a
     custom claims provider (ADR-0019).
3. **Control plane: the stamp user and role.** Call the control plane as an operator with the
   `venues:write` scope:

   ```http
   POST /venues/{venue_id}/users
   {"external_subject": "<oid>", "email": "gm@venue.example", "display_name": "GM", "role": "manager"}
   ```

   - Roles:
     - `owner`, `manager`: decide drafts and connect systems;
     - `approver`: decide drafts;
     - `viewer`: read only.
   - `"tenant_wide": true` grants the role on every venue of the tenant.
   - Repeating the call is safe. An existing user keeps their name and email (no updates yet).
   - A user who was disabled stays disabled, even though the call returns 201.
   - Don't add one `oid` to two tenants: one tenant per user isn't enforced yet.

## Check

The user signs in. `GET /me` lists the venue with the role.

## Not yet possible (needs its own grants and review)

Changing a user's email or name, removing a role, and disabling a user.
