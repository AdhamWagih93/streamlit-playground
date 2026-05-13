FROM php:8.1-fpm

ENV DEBIAN_FRONTEND=noninteractive \
    COMPOSER_ALLOW_SUPERUSER=1 \
    COMPOSER_MEMORY_LIMIT=-1

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

COPY --from=composer:2.2 /usr/bin/composer /usr/bin/composer

COPY docker/app/php.ini   /usr/local/etc/php/conf.d/zz-magento.ini
COPY docker/app/www.conf  /usr/local/etc/php-fpm.d/www.conf

WORKDIR /var/www/html
COPY . /var/www/html

# If the on-prem Nexus is reachable, replace this block with:
#   RUN composer config secure-http false \
#    && composer config disable-tls true \
#    && composer config repositories.packagist.org composer http://ef-nexus-01:8090/repository/repo-magento/ \
#    && composer install --no-dev --optimize-autoloader --no-interaction
RUN composer install --no-dev --optimize-autoloader --no-interaction

RUN usermod -u 1000 www-data && groupmod -g 1000 www-data \
    && chown -R www-data:www-data /var/www/html

EXPOSE 9000
CMD ["php-fpm"]
