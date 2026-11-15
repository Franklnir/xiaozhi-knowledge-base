package db

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type DB struct {
	Pool *pgxpool.Pool
}

func NewPostgresPool(ctx context.Context, dsn string) (*DB, error) {
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, fmt.Errorf("unable to parse database config: %w", err)
	}

	cfg.MaxConns = 25
	cfg.MinConns = 3
	cfg.MaxConnLifetime = 30 * time.Minute
	cfg.MaxConnIdleTime = 5 * time.Minute

	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("unable to create connection pool: %w", err)
	}

	// Ping to verify
	pingCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if err := pool.Ping(pingCtx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("database ping failed: %w", err)
	}

	log.Printf("[DB] Connected to PostgreSQL successfully (MaxConns=%d, MinConns=%d)", cfg.MaxConns, cfg.MinConns)
	return &DB{Pool: pool}, nil
}

func (d *DB) Close() {
	if d.Pool != nil {
		d.Pool.Close()
		log.Println("[DB] Connection pool closed.")
	}
}

// SetSystemContext sets RLS session variables so the gateway can query data safely as system role
func (d *DB) SetSystemContext(ctx context.Context, tx pgx.Tx) error {
	_, err := tx.Exec(ctx, `
		SELECT set_config('app.user_role', 'system', true),
		       set_config('app.is_admin', 'true', true);
	`)
	return err
}

// SetAccountContext scopes transactions to the authenticated account.
// A zero owner is used to perform a service-side device or room lookup.
func (d *DB) SetAccountContext(ctx context.Context, tx pgx.Tx, ownerID int64) error {
	if _, err := tx.Exec(ctx, "SET LOCAL ROLE app_tenant_role"); err != nil {
		return err
	}
	if ownerID == 0 {
		return d.SetSystemContext(ctx, tx)
	}
	_, err := tx.Exec(ctx, `
		SELECT set_config('app.user_id', $1, true),
		       set_config('app.current_user_id', $1, true),
		       set_config('app.user_role', 'user', true),
		       set_config('app.is_admin', 'false', true);
	`, fmt.Sprintf("%d", ownerID))
	return err
}

// SetUserContext sets RLS session variables for a specific user
func (d *DB) SetUserContext(ctx context.Context, tx pgx.Tx, userID int64) error {
	_, err := tx.Exec(ctx, `
		SELECT set_config('app.user_id', $1, true),
		       set_config('app.current_user_id', $1, true),
		       set_config('app.user_role', 'system', true),
		       set_config('app.is_admin', 'true', true);
	`, fmt.Sprintf("%d", userID))
	return err
}
