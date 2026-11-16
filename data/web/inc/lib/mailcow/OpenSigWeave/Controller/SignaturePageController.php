<?php

declare(strict_types=1);

namespace Mailcow\OpenSigWeave\Controller;

use Mailcow\Core\AccessDenied;
use Mailcow\Core\Context;
use Mailcow\OpenSigWeave\Service\DomainAccessService;
use Mailcow\OpenSigWeave\Service\EngineClient;
use Mailcow\OpenSigWeave\Service\EngineClientException;
use Throwable;

final class SignaturePageController
{
    public function __construct(
        private DomainAccessService $domainAccess,
        private EngineClient $engine,
        private Context $context
    ) {
    }

    /** @return array<string, mixed> */
    public function build(string $basePath): array
    {
        $domains = $this->domainAccess->manageableDomains();
        $hasAccess = $this->context->isAdmin()
            || ($this->context->isDomainAdmin() && $this->context->hasAcl('opensigweave'));

        if (empty($_SESSION['opensigweave_csrf'])) {
            $_SESSION['opensigweave_csrf'] = bin2hex(random_bytes(32));
        }
        $csrfToken = (string)$_SESSION['opensigweave_csrf'];
        $successMessage = $_SESSION['opensigweave_flash'] ?? null;
        unset($_SESSION['opensigweave_flash']);
        $errorMessage = null;
        $previewHtml = null;
        $previewSource = null;
        $createName = '';
        $createMode = 'AUTO';
        $createHtml = '';

        $selectedDomain = trim((string)($_POST['domain'] ?? $_GET['domain'] ?? ($domains[0] ?? '')));
        if ($selectedDomain !== '' && !in_array($selectedDomain, $domains, true)) {
            $selectedDomain = '';
            $errorMessage = 'Für diese Mailcow-Domain besteht keine OpenSigWeave-Berechtigung.';
        }

        if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST') {
            try {
                $submittedToken = (string)($_POST['csrf_token'] ?? '');
                if (!hash_equals($csrfToken, $submittedToken)) {
                    throw new AccessDenied();
                }

                $selectedDomain = trim((string)($_POST['domain'] ?? ''));
                $this->domainAccess->requireManageableDomain($selectedDomain);
                $action = (string)($_POST['action'] ?? '');
                $signatureId = strtolower(trim((string)($_POST['signature_id'] ?? '')));
                if ($signatureId !== '' && !preg_match('/^[a-f0-9]{32}$/', $signatureId)) {
                    throw new \InvalidArgumentException('Ungültige Signatur-ID.');
                }

                switch ($action) {
                    case 'create':
                        $name = trim((string)($_POST['name'] ?? ''));
                        $mode = strtoupper(trim((string)($_POST['mode'] ?? 'AUTO')));
                        $html = (string)($_POST['html_content'] ?? '');
                        if ($name === '' || mb_strlen($name, 'UTF-8') > 255) {
                            throw new \InvalidArgumentException('Bitte gib einen gültigen Signaturnamen ein.');
                        }
                        if (!in_array($mode, ['AUTO', 'PROFESSIONAL', 'INTERNAL', 'NONE'], true)) {
                            throw new \InvalidArgumentException('Ungültiger Signaturmodus.');
                        }
                        if ($html === '' || strlen($html) > 65536) {
                            throw new \InvalidArgumentException('Der HTML-Inhalt darf nicht leer sein und maximal 64 KiB umfassen.');
                        }
                        $created = $this->engine->createDomainSignature($selectedDomain, [
                            'name' => $name,
                            'mode' => $mode,
                            'html_content' => $html,
                            'created_by' => $this->context->username,
                        ]);
                        $_SESSION['opensigweave_flash'] = 'Signatur wurde angelegt.';
                        $this->redirect($basePath, $selectedDomain, (string)$created['id']);
                    case 'save_version':
                        if ($signatureId === '') {
                            throw new \InvalidArgumentException('Keine Signatur ausgewählt.');
                        }
                        $html = (string)($_POST['html_content'] ?? '');
                        if ($html === '' || strlen($html) > 65536) {
                            throw new \InvalidArgumentException('Der HTML-Inhalt darf nicht leer sein und maximal 64 KiB umfassen.');
                        }
                        $this->engine->addDomainSignatureVersion($selectedDomain, $signatureId, [
                            'html_content' => $html,
                            'created_by' => $this->context->username,
                        ]);
                        $_SESSION['opensigweave_flash'] = 'Neue Signaturversion wurde gespeichert.';
                        $this->redirect($basePath, $selectedDomain, $signatureId);
                    case 'preview':
                        $html = (string)($_POST['html_content'] ?? '');
                        if ($html === '' || strlen($html) > 65536) {
                            throw new \InvalidArgumentException('Der HTML-Inhalt darf nicht leer sein und maximal 64 KiB umfassen.');
                        }
                        if ($signatureId !== '') {
                            $this->engine->getDomainSignature($selectedDomain, $signatureId);
                        }
                        $preview = $this->engine->previewDomainSignature($selectedDomain, $html);
                        $previewHtml = (string)($preview['html'] ?? '');
                        $previewSource = $html;
                        $createName = trim((string)($_POST['name'] ?? ''));
                        $createMode = strtoupper(trim((string)($_POST['mode'] ?? 'AUTO')));
                        $createHtml = $html;
                        $successMessage = 'Die Vorschau wurde mit Beispieldaten erstellt.';
                        break;
                    case 'toggle':
                        if ($signatureId === '') {
                            throw new \InvalidArgumentException('Keine Signatur ausgewählt.');
                        }
                        $isActive = (string)($_POST['is_active'] ?? '0') === '1';
                        $this->engine->updateDomainSignature($selectedDomain, $signatureId, $isActive);
                        $_SESSION['opensigweave_flash'] = $isActive
                            ? 'Signatur wurde aktiviert.'
                            : 'Signatur wurde deaktiviert.';
                        $this->redirect($basePath, $selectedDomain, $signatureId);
                    case 'restore':
                        if ($signatureId === '') {
                            throw new \InvalidArgumentException('Keine Signatur ausgewählt.');
                        }
                        $versionId = strtolower(trim((string)($_POST['version_id'] ?? '')));
                        if (!preg_match('/^[a-f0-9]{32}$/', $versionId)) {
                            throw new \InvalidArgumentException('Ungültige Versions-ID.');
                        }
                        $this->engine->restoreDomainSignatureVersion($selectedDomain, $signatureId, $versionId);
                        $_SESSION['opensigweave_flash'] = 'Version wurde als neue aktuelle Version wiederhergestellt.';
                        $this->redirect($basePath, $selectedDomain, $signatureId);
                    default:
                        throw new \InvalidArgumentException('Unbekannte Aktion.');
                }
            } catch (AccessDenied) {
                $errorMessage = 'Die Aktion wurde wegen fehlender Berechtigung oder ungültigem CSRF-Token abgelehnt.';
            } catch (EngineClientException $exception) {
                $errorMessage = match ($exception->httpStatus) {
                    404 => 'Die Domain, Signatur oder Version wurde nicht gefunden.',
                    409 => 'Eine Signatur mit diesem Namen existiert bereits.',
                    422 => 'Der Signaturinhalt oder die übermittelten Angaben sind ungültig.',
                    default => 'Der interne OpenSigWeave-Dienst konnte die Aktion nicht abschließen.',
                };
            } catch (Throwable $exception) {
                error_log('OpenSigWeave UI: ' . $exception->getMessage());
                $errorMessage = $exception instanceof \InvalidArgumentException
                    ? $exception->getMessage()
                    : 'Die OpenSigWeave-Aktion ist fehlgeschlagen.';
            }
        }

        $signatures = [];
        $selectedSignature = null;
        $versions = [];
        if ($selectedDomain !== '') {
            try {
                $listed = $this->engine->listDomainSignatures($selectedDomain);
                $signatures = is_array($listed['signatures'] ?? null) ? $listed['signatures'] : [];

                $requestedSignature = strtolower(trim((string)($_GET['signature'] ?? $_POST['signature_id'] ?? '')));
                if ($requestedSignature !== '' && preg_match('/^[a-f0-9]{32}$/', $requestedSignature)) {
                    $selectedSignature = $this->engine->getDomainSignature($selectedDomain, $requestedSignature);
                    if ($previewSource !== null) {
                        $selectedSignature['html_content'] = $previewSource;
                    }
                    $versionList = $this->engine->listDomainSignatureVersions($selectedDomain, $requestedSignature);
                    $versions = is_array($versionList['versions'] ?? null) ? $versionList['versions'] : [];
                }
            } catch (EngineClientException $exception) {
                $errorMessage = 'Der interne OpenSigWeave-Dienst ist derzeit nicht erreichbar oder hat die Anfrage abgelehnt.';
                error_log('OpenSigWeave engine HTTP status: ' . $exception->httpStatus);
            } catch (Throwable $exception) {
                $errorMessage = 'Der interne OpenSigWeave-Dienst ist derzeit nicht erreichbar.';
                error_log('OpenSigWeave engine error: ' . $exception->getMessage());
            }
        }

        return [
            'domains' => $domains,
            'has_access' => $hasAccess,
            'is_admin' => $this->context->isAdmin(),
            'selected_domain' => $selectedDomain,
            'signatures' => $signatures,
            'selected_signature' => $selectedSignature,
            'versions' => $versions,
            'preview_html' => $previewHtml,
            'create_name' => $createName,
            'create_mode' => $createMode,
            'create_html' => $createHtml,
            'csrf_token' => $csrfToken,
            'success_message' => $successMessage,
            'error_message' => $errorMessage,
        ];
    }

    private function redirect(string $basePath, string $domain, string $signatureId): never
    {
        header(
            'Location: ' . $basePath . '?domain=' . rawurlencode($domain)
            . '&signature=' . rawurlencode($signatureId),
            true,
            303
        );
        exit;
    }
}
