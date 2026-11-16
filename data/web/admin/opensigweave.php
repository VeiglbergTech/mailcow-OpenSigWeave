<?php
require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/prerequisites.inc.php';
require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/triggers.admin.inc.php';

protect_route(['admin']);

require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/header.inc.php';
$_SESSION['return_to'] = $_SERVER['REQUEST_URI'];

$context = \Mailcow\Core\Context::fromSession($_SESSION);
$domain_access = new \Mailcow\OpenSigWeave\Service\DomainAccessService(
  new \Mailcow\OpenSigWeave\Repository\DomainRepository($pdo),
  $context
);

$template = 'opensigweave.twig';
$template_data = [
  'domains' => $domain_access->manageableDomains(),
  'has_access' => true,
  'is_admin' => true,
];

require_once $_SERVER['DOCUMENT_ROOT'] . '/inc/footer.inc.php';
