FROM php:8.1-apache

ENV DEBIAN_FRONTEND=noninteractive \
    COMPOSER_ALLOW_SUPERUSER=1 \
    COMPOSER_MEMORY_LIMIT=-1 \
    APACHE_DOCUMENT_ROOT=/var/www/html/pub

# System packages + PHP extensions Magento 2 requires
RUN apt-get update && apt-get install -y --no-install-recommends \
        git unzip curl ca-certificates \
        libfreetype6-dev libjpeg62-turbo-dev libpng-dev libwebp-dev \
        libicu-dev libxml2-dev libxslt1-dev libzip-dev \
        libonig-dev libsodium-dev libssl-dev \
        libcurl4-openssl-dev \
    && docker-php-ext-configure gd --with-freetype --with-jpeg --with-webp \
    && docker-php-ext-configure intl \
    && docker-php-ext-install -j"$(nproc)" \
        bcmath gd intl mbstring opcache pdo_mysql soap sockets sodium xsl zip \
    && pecl install redis \
    && docker-php-ext-enable redis \
    && rm -rf /var/lib/apt/lists/*

# Apache modules Magento needs (.htaccess rewrites, far-future headers, gzip)
RUN a2enmod rewrite headers expires deflate

# Point Apache's DocumentRoot at Magento's pub/ and allow .htaccess overrides
RUN sed -ri -e 's!/var/www/html!${APACHE_DOCUMENT_ROOT}!g' \
        /etc/apache2/sites-available/*.conf \
        /etc/apache2/apache2.conf \
        /etc/apache2/conf-available/*.conf \
 && sed -ri -e 's!AllowOverride None!AllowOverride All!g' /etc/apache2/apache2.conf

COPY --from=composer:2.2 /usr/bin/composer /usr/bin/composer

COPY docker/app/php.ini /usr/local/etc/php/conf.d/zz-magento.ini

WORKDIR /var/www/html
COPY . /var/www/html

# If the on-prem Nexus is reachable, prepend:
#   composer config secure-http false \
#    && composer config disable-tls true \
#    && composer config repositories.packagist.org composer http://ef-nexus-01:8090/repository/repo-magento/ \
#    &&
RUN composer install --no-dev --optimize-autoloader --no-interaction

RUN usermod -u 1000 www-data && groupmod -g 1000 www-data \
    && chown -R www-data:www-data /var/www/html

EXPOSE 80
# Base image CMD already runs apache2-foreground.
