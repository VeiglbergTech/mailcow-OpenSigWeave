<?php

declare(strict_types=1);

namespace Mailcow\Tests\OpenSigWeave\Service;

use Mailcow\Core\AccessDenied;
use Mailcow\OpenSigWeave\Repository\DomainRepository;
use Mailcow\OpenSigWeave\Service\DomainAccessService;
use Mailcow\Tests\Support\FakeContext;
use PHPUnit\Framework\MockObject\MockObject;
use PHPUnit\Framework\TestCase;

class DomainAccessServiceTest extends TestCase
{
    private DomainRepository&MockObject $domains;

    protected function setUp(): void
    {
        $this->domains = $this->createMock(DomainRepository::class);
    }

    public function testAdminGetsAllActiveMailcowDomainsAndCanManageThem(): void
    {
        $context = FakeContext::admin();
        $this->domains->expects($this->once())
            ->method('listManageable')
            ->with($context)
            ->willReturn(['example.org', 'example.net']);
        $this->domains->expects($this->once())
            ->method('canManage')
            ->with($context, 'example.org')
            ->willReturn(true);

        $service = new DomainAccessService($this->domains, $context);

        self::assertSame(['example.org', 'example.net'], $service->manageableDomains());
        self::assertTrue($service->canManageDomain('example.org'));
        $service->requireManageableDomain('example.org');
    }

    public function testDomainAdminIsLimitedToAssignedMailcowDomains(): void
    {
        $context = FakeContext::domainAdmin([], ['managed.example.org']);
        $this->domains->expects($this->once())
            ->method('listManageable')
            ->with($context)
            ->willReturn(['managed.example.org']);
        $this->domains->expects($this->exactly(2))
            ->method('canManage')
            ->willReturnCallback(
                static fn($actualContext, string $domain): bool =>
                    $actualContext === $context && $domain === 'managed.example.org'
            );

        $service = new DomainAccessService($this->domains, $context);

        self::assertSame(['managed.example.org'], $service->manageableDomains());
        self::assertTrue($service->canManageDomain('managed.example.org'));
        self::assertFalse($service->canManageDomain('foreign.example.org'));
    }

    public function testInactiveOrUnassignedDomainIsDenied(): void
    {
        $context = FakeContext::domainAdmin([], ['managed.example.org']);
        $this->domains->method('canManage')->willReturn(false);

        $service = new DomainAccessService($this->domains, $context);

        self::assertFalse($service->canManageDomain('managed.example.org'));
        $this->expectException(AccessDenied::class);
        $service->requireManageableDomain('managed.example.org');
    }

    public function testMailboxUserCannotListOrManageDomains(): void
    {
        $context = FakeContext::user();
        $this->domains->expects($this->never())->method('listManageable');
        $this->domains->expects($this->never())->method('canManage');

        $service = new DomainAccessService($this->domains, $context);

        self::assertSame([], $service->manageableDomains());
        self::assertFalse($service->canManageDomain('example.org'));
        $this->expectException(AccessDenied::class);
        $service->requireManageableDomain('example.org');
    }

    public function testEmptyDomainIsDeniedWithoutDatabaseLookup(): void
    {
        $context = FakeContext::admin();
        $this->domains->expects($this->never())->method('canManage');

        $service = new DomainAccessService($this->domains, $context);

        self::assertFalse($service->canManageDomain(''));
        $this->expectException(AccessDenied::class);
        $service->requireManageableDomain('');
    }
}
