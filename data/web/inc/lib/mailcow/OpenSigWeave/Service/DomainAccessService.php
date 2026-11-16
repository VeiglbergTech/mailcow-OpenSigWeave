<?php

declare(strict_types=1);

namespace Mailcow\OpenSigWeave\Service;

use Mailcow\Core\AccessDenied;
use Mailcow\Core\Context;
use Mailcow\OpenSigWeave\Repository\DomainRepository;

/**
 * Server-side domain authorization for every OpenSigWeave management operation.
 *
 * UI filtering is not an authorization boundary: write/read handlers must call
 * requireManageableDomain() again for the requested domain.
 */
class DomainAccessService
{
    public function __construct(
        private DomainRepository $domains,
        private Context $context
    ) {
    }

    /** @return string[] active Mailcow domains visible to this caller */
    public function manageableDomains(): array
    {
        if (!$this->context->isAdmin() && !$this->context->isDomainAdmin()) {
            return [];
        }

        return $this->domains->listManageable($this->context);
    }

    public function canManageDomain(string $domain): bool
    {
        if ($domain === '' || (!$this->context->isAdmin() && !$this->context->isDomainAdmin())) {
            return false;
        }

        return $this->domains->canManage($this->context, $domain);
    }

    /** @throws AccessDenied when the domain is missing, inactive or outside the caller's scope */
    public function requireManageableDomain(string $domain): void
    {
        if (!$this->canManageDomain($domain)) {
            throw new AccessDenied();
        }
    }
}
