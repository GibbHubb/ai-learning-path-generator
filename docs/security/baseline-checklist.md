<!-- Source: OWASP Application Security Verification Standard, L1 baseline -->

# Security baseline checklist

## Authentication

- [ ] Passwords are hashed with a strong adaptive algorithm (bcrypt, argon2)
- [ ] Minimum password length enforced (8+ characters)
- [ ] Breached password checking or dictionary check in place
- [ ] 2FA/MFA supported for all user accounts
- [ ] Account lockout or rate limiting after repeated failed login attempts
- [ ] Password reset uses a time-limited, single-use token
- [ ] Authentication credentials are never logged or exposed in URLs

## Authorization and access control

- [ ] Every API endpoint enforces authorization checks
- [ ] Role-based access control (RBAC) is implemented and tested
- [ ] Tenant isolation enforced at the data layer (users cannot access other tenants' data)
- [ ] Default-deny: access is denied unless explicitly granted
- [ ] Admin functions are restricted to admin roles only

## Session management

- [ ] Session tokens are generated with sufficient entropy
- [ ] Sessions expire after a reasonable idle timeout
- [ ] Session tokens are invalidated on logout
- [ ] Session fixation is prevented (new token on login)
- [ ] Cookies use Secure, HttpOnly, and SameSite attributes

## Input validation and output encoding

- [ ] All user input is validated on the server side
- [ ] Output is context-aware encoded to prevent XSS
- [ ] Parameterized queries or ORM used to prevent SQL injection
- [ ] File uploads are validated (type, size, content) and stored safely
- [ ] URL redirects are validated against an allowlist

## Cryptography

- [ ] Data at rest is encrypted where required
- [ ] Data in transit uses TLS 1.2+
- [ ] No hardcoded secrets, keys, or credentials in source code
- [ ] Secrets are managed via environment variables or a secrets manager
- [ ] Cryptographic keys are rotated on a defined schedule

## Error handling and logging

- [ ] Error responses do not leak internal details (stack traces, SQL, paths)
- [ ] All authentication and authorization failures are logged
- [ ] Sensitive actions (role changes, data exports, deletions) have an audit trail
- [ ] Logs do not contain sensitive data (passwords, tokens, PII)
- [ ] Centralized logging is in place for production

## Data protection

- [ ] PII is identified and handled according to policy
- [ ] Data retention policy is defined and enforced
- [ ] User data can be exported and deleted on request
- [ ] Backups are encrypted and tested for restoration
- [ ] Database access is restricted to application service accounts

## Communications security

- [ ] TLS is enforced on all external endpoints
- [ ] HSTS header is set with a reasonable max-age
- [ ] Internal service-to-service communication is authenticated
- [ ] Certificate validity is monitored

## Malicious code and dependencies

- [ ] Dependency scanning (SCA) runs in CI
- [ ] Dependencies are pinned and updated on a defined cadence
- [ ] No known critical/high CVEs in production dependencies
- [ ] Third-party code is reviewed before adoption

## Business logic

- [ ] Rate limiting is applied to public and sensitive endpoints
- [ ] Abuse prevention measures are in place (e.g., CAPTCHAs, honeypots)
- [ ] Business-critical flows have server-side validation (not client-only)
- [ ] Idempotency is enforced where needed (payments, state transitions)

## Configuration

- [ ] Production environment is hardened (debug mode off, default credentials removed)
- [ ] Secrets are not committed to version control
- [ ] Environment separation is enforced (dev/staging/prod)
- [ ] Security headers are set (CSP, X-Content-Type-Options, X-Frame-Options)
- [ ] Unnecessary services, ports, and features are disabled
