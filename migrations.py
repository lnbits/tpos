from lnbits.db import Database


async def m001_initial(db: Database):
    """
    Initial tposs table.
    """
    await db.execute(
        """
        CREATE TABLE tpos.tposs (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            name TEXT NOT NULL,
            currency TEXT NOT NULL
        );
    """
    )


async def m002_addtip_wallet(db: Database):
    """
    Add tips to tposs table
    """
    await db.execute(
        """
        ALTER TABLE tpos.tposs ADD tip_wallet TEXT NULL;
    """
    )


async def m003_addtip_options(db: Database):
    """
    Add tips to tposs table
    """
    await db.execute(
        """
        ALTER TABLE tpos.tposs ADD tip_options TEXT NULL;
    """
    )


async def m004_addwithdrawlimit(db: Database):
    await db.execute(
        """
        CREATE TABLE tpos.pos (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            name TEXT NOT NULL,
            currency TEXT NOT NULL,
            tip_wallet TEXT NULL,
            tip_options TEXT NULL,
            withdrawlimit INTEGER DEFAULT 0,
            withdrawpin INTEGER DEFAULT 878787,
            withdrawamt INTEGER DEFAULT 0,
            withdrawtime INTEGER NOT NULL DEFAULT 0,
            withdrawbtwn INTEGER NOT NULL DEFAULT 10
        );
    """
    )
    result = await db.execute("SELECT * FROM tpos.tposs")
    rows = result.mappings().all()
    for row in rows:
        await db.execute(
            """
            INSERT INTO tpos.pos (
                id,
                wallet,
                name,
                currency,
                tip_wallet,
                tip_options
            )
            VALUES (:id, :wallet, :name, :currency, :tip_wallet, :tip_options)
            """,
            {
                "id": row["id"],
                "wallet": row["wallet"],
                "name": row["name"],
                "currency": row["currency"],
                "tip_wallet": row["tip_wallet"],
                "tip_options": row["tip_options"],
            },
        )
    await db.execute("DROP TABLE tpos.tposs")


async def m005_initial(db: Database):
    """
    Initial withdraws table.
    """
    await db.execute(
        """
        CREATE TABLE tpos.withdraws (
            id TEXT PRIMARY KEY,
            tpos_id TEXT NOT NULL,
            amount int,
            claimed BOOLEAN DEFAULT false
        );
    """
    )


async def m006_items(db: Database):
    """
    Add items to tpos table for storing various items (JSON format)
    See `Item` class in models.
    """
    await db.execute(
        """
        ALTER TABLE tpos.pos ADD items TEXT DEFAULT '[]';
        """
    )


async def m007_atm_premium(db: Database):
    """
    Add a premium % to ATM withdraws
    """
    await db.execute("ALTER TABLE tpos.pos ADD COLUMN withdrawpremium FLOAT;")


async def m008_atm_time_option_and_pin_toggle(db: Database):
    """
    Add a time mins/sec and pin toggle
    """
    await db.execute(
        "ALTER TABLE tpos.pos " "ADD COLUMN withdrawtimeopt TEXT DEFAULT 'mins'"
    )
    await db.execute(
        "ALTER TABLE tpos.pos "
        "ADD COLUMN withdrawpindisabled BOOL NOT NULL DEFAULT false"
    )


async def m009_tax_inclusive(db: Database):
    """
    Add tax_inclusive column
    """
    await db.execute(
        "ALTER TABLE tpos.pos ADD COLUMN tax_inclusive BOOL NOT NULL DEFAULT true;"
    )
    await db.execute("ALTER TABLE tpos.pos ADD COLUMN tax_default FLOAT DEFAULT 0;")


async def m010_rename_tpos_withdraw_columns(db: Database):
    """
    Add rename tpos withdraw columns
    """
    await db.execute(
        """
        CREATE TABLE tpos.pos_backup AS
        SELECT
        id, name, currency, items, wallet, tax_inclusive,
        tax_default, tip_wallet, tip_options,
        withdrawamt AS withdrawn_amount,
        withdrawtime AS withdraw_time,
        withdrawbtwn AS withdraw_between,
        withdrawlimit AS withdraw_limit,
        withdrawtimeopt AS withdraw_time_option,
        withdrawpremium AS withdraw_premium,
        withdrawpindisabled AS withdraw_pin_disabled,
        withdrawpin AS withdraw_pin
        FROM tpos.pos
        """
    )
    await db.execute("DROP TABLE tpos.pos")
    await db.execute("ALTER TABLE tpos.pos_backup RENAME TO pos")


async def m011_lnaddress(db: Database):
    """
    Add lnaddress to tpos table
    """
    await db.execute(
        """
        ALTER TABLE tpos.pos ADD lnaddress BOOLEAN DEFAULT false;
    """
    )


async def m012_addlnaddress(db: Database):
    """
    Add lnaddress_cut to tpos table
    """
    await db.execute(
        """
        ALTER TABLE tpos.pos ADD lnaddress_cut INTEGER NULL;
    """
    )


async def m013_add_receipt_print(db: Database):
    """
    Add enable_receipt_print to tpos table
    """
    await db.execute(
        """
        ALTER TABLE tpos.pos ADD enable_receipt_print BOOLEAN DEFAULT false;
    """
    )

    await db.execute("ALTER TABLE tpos.pos ADD business_name TEXT;")
    await db.execute("ALTER TABLE tpos.pos ADD business_address TEXT;")
    await db.execute("ALTER TABLE tpos.pos ADD business_vat_id TEXT;")
