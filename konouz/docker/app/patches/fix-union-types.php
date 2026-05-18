<?php
/**
 * Patch magento/framework 103.0.x EntityAbstract so its code generator
 * stops crashing on PHP 8.0+ union / intersection types.
 *
 * Before: $type->getName()        # fatal for ReflectionUnionType
 * After:  union-safe equivalent that stringifies the type
 *
 * Idempotent: re-running is a no-op once the marker is present.
 */

declare(strict_types=1);

$file = '/var/www/html/vendor/magento/framework/Code/Generator/EntityAbstract.php';

if (!is_file($file)) {
    fwrite(STDERR, "EntityAbstract.php not found at $file\n");
    exit(1);
}

$src = file_get_contents($file);

if (strpos($src, '/*UNION_TYPE_PATCH*/') !== false) {
    echo "Already patched.\n";
    exit(0);
}

// Wrap every $var->getName() call in an instanceof guard. Safe for non-
// Reflection variables since the union/intersection check is false for them
// and the original ->getName() call is preserved.
$patched = preg_replace(
    '/(\$[a-zA-Z_][a-zA-Z0-9_]*)->getName\(\)/',
    '($1 instanceof \\ReflectionUnionType || $1 instanceof \\ReflectionIntersectionType ? (string)$1 : $1->getName())/*UNION_TYPE_PATCH*/',
    $src
);

if ($patched === null) {
    fwrite(STDERR, "preg_replace failed: " . preg_last_error() . "\n");
    exit(1);
}

if ($patched === $src) {
    fwrite(STDERR, "No matches; framework version may not need this patch.\n");
    exit(1);
}

file_put_contents($file, $patched);
echo "EntityAbstract.php patched (union/intersection types now handled).\n";
