-- Bootstrap minimo para entorno local de Iteracion 1.
-- Nota: este script NO reemplaza el dataset oficial completo.

CREATE TABLE IF NOT EXISTS actor (
    actor_id SERIAL PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS film (
    film_id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    release_year INTEGER
);

CREATE TABLE IF NOT EXISTS rental (
    rental_id SERIAL PRIMARY KEY,
    rental_date TIMESTAMP NOT NULL DEFAULT NOW(),
    inventory_id INTEGER,
    customer_id INTEGER,
    return_date TIMESTAMP
);

INSERT INTO actor (first_name, last_name)
SELECT 'PENELOPE', 'GUINESS'
WHERE NOT EXISTS (SELECT 1 FROM actor);

INSERT INTO film (title, release_year)
SELECT 'ACADEMY DINOSAUR', 2006
WHERE NOT EXISTS (SELECT 1 FROM film);

INSERT INTO rental (inventory_id, customer_id)
SELECT 1, 1
WHERE NOT EXISTS (SELECT 1 FROM rental);
