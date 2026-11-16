<?php

declare(strict_types=1);

namespace Mailcow\Tests\OpenSigWeave\Repository;

use Mailcow\Core\Context;
use Mailcow\OpenSigWeave\Repository\DomainRepository;
use PDO;
use PHPUnit\Framework\TestCase;

class DomainRepositoryTest extends TestCase
{
    private PDO $pdo;
    private DomainRepository $repository;

    protected function setUp(): void
    {
        if (!extension_loaded('pdo_mysql')) {
            self::markTestSkipped('PDO MySQL is required for MariaDB integration tests');
        }

        $host = getenv('OPENSIGWEAVE_DB_HOST');
        if (!$host) {
            self::markTestSkipped('MariaDB integration connection is not configured');
        }

        $port = (int)(getenv('OPENSIGWEAVE_DB_PORT') ?: 3306);
        $database = getenv('DBNAME') ?: 'mailcow';
        $username = getenv('DBUSER') ?: 'mailcow';
        $password = getenv('DBPASS') ?: '';

        $this->pdo = new PDO(
            sprintf('mysql:host=%s;port=%d;dbname=%s;charset=utf8mb4', $host, $port, $database),
            $username,
            $password,
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
        );

        // Temporary tables shadow any real Mailcow tables and disappear with this connection.
        $this->pdo->exec(
            "CREATE TEMPORARY TABLE `domain` (
                `domain` VARCHAR(255) NOT NULL PRIMARY KEY,
                `active` TINYINT(1) NOT NULL DEFAULT 1
            ) ENGINE=InnoDB"
        );
        $this->pdo->exec(
            "CREATE TEMPORARY TABLE `domain_admins` (
                `id` INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                `username` VARCHAR(255) NOT NULL,
                `domain` VARCHAR(255) NOT NULL,
                `active` TINYINT(1) NOT NULL DEFAULT 1
            ) ENGINE=InnoDB"
        );

        $domains = $this->pdo->prepare("INSERT INTO `domain` (`domain`, `active`) VALUES (?, ?)");
        foreach ([
            ['active.example.org', 1],
            ['disabled.example.org', 0],
            ['managed.example.org', 1],
            ['inactive-assignment.example.org', 1],
            ['unassigned.example.org', 1],
        ] as [$domain, $active]) {
            $domains->execute([$domain, $active]);
        }

        $assignments = $this->pdo->prepare(
            "INSERT INTO `domain_admins` (`username`, `domain`, `active`) VALUES (?, ?, ?)"
        );
        $assignments->execute(['da@example.org', 'managed.example.org', 1]);
        $assignments->execute(['da@example.org', 'inactive-assignment.example.org', 0]);

        $this->repository = new DomainRepository($this->pdo);
    }

    public function testAdminCanOnlyManageActiveMailcowDomains(): void
    {
        $context = new Context('admin', 'admin');

        self::assertSame(
            ['active.example.org', 'inactive-assignment.example.org', 'managed.example.org', 'unassigned.example.org'],
            $this->repository->listManageable($context)
        );
        self::assertTrue($this->repository->canManage($context, 'active.example.org'));
        self::assertFalse($this->repository->canManage($context, 'disabled.example.org'));
        self::assertFalse($this->repository->canManage($context, 'missing.example.org'));
    }

    public function testDomainAdminCanOnlyManageActivelyAssignedDomains(): void
    {
        $context = new Context('domainadmin', 'da@example.org');

        self::assertSame(['managed.example.org'], $this->repository->listManageable($context));
        self::assertTrue($this->repository->canManage($context, 'managed.example.org'));
        self::assertFalse($this->repository->canManage($context, 'inactive-assignment.example.org'));
        self::assertFalse($this->repository->canManage($context, 'unassigned.example.org'));
        self::assertFalse($this->repository->canManage($context, 'disabled.example.org'));
    }

    public function testMailboxUserCannotManageDomains(): void
    {
        $context = new Context('user', 'user@managed.example.org');

        self::assertSame([], $this->repository->listManageable($context));
        self::assertFalse($this->repository->canManage($context, 'managed.example.org'));
    }
}
