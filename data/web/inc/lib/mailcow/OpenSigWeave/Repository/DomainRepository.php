<?php

declare(strict_types=1);

namespace Mailcow\OpenSigWeave\Repository;

use Mailcow\Core\Context;
use Mailcow\Core\Repository;
use PDO;

/**
 * Reads canonical Mailcow domains and their domain-administrator assignments.
 *
 * OpenSigWeave never creates or activates mail domains. A domain is manageable only
 * while the Mailcow domain and, for domain administrators, its assignment are active.
 */
class DomainRepository extends Repository
{
    /** @return string[] active domains manageable by the caller */
    public function listManageable(Context $context): array
    {
        if ($context->isAdmin()) {
            return $this->query(
                "SELECT d.`domain` FROM `domain` d WHERE d.`active` = '1' ORDER BY d.`domain`"
            )->fetchAll(PDO::FETCH_COLUMN);
        }

        if ($context->isDomainAdmin() && $context->username !== null && $context->username !== '') {
            return $this->query(
                "SELECT d.`domain`
                 FROM `domain` d
                 INNER JOIN `domain_admins` da ON da.`domain` = d.`domain`
                 WHERE d.`active` = '1'
                   AND da.`active` = '1'
                   AND da.`username` = :username
                 ORDER BY d.`domain`",
                ['username' => $context->username]
            )->fetchAll(PDO::FETCH_COLUMN);
        }

        return [];
    }

    /** True only for an active Mailcow domain within the caller's assigned scope */
    public function canManage(Context $context, string $domain): bool
    {
        if ($domain === '') {
            return false;
        }

        if ($context->isAdmin()) {
            return (bool)$this->query(
                "SELECT 1 FROM `domain` d
                 WHERE d.`domain` = :domain AND d.`active` = '1'
                 LIMIT 1",
                ['domain' => $domain]
            )->fetchColumn();
        }

        if ($context->isDomainAdmin() && $context->username !== null && $context->username !== '') {
            return (bool)$this->query(
                "SELECT 1
                 FROM `domain` d
                 INNER JOIN `domain_admins` da ON da.`domain` = d.`domain`
                 WHERE d.`domain` = :domain
                   AND d.`active` = '1'
                   AND da.`active` = '1'
                   AND da.`username` = :username
                 LIMIT 1",
                ['domain' => $domain, 'username' => $context->username]
            )->fetchColumn();
        }

        return false;
    }
}
