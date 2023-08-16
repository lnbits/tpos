import datetime

async def m001_initial(db):
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


async def m002_addtip_wallet(db):
    """
    Add tips to tposs table
    """
    await db.execute(
        """
        ALTER TABLE tpos.tposs ADD tip_wallet TEXT NULL;
    """
    )


async def m003_addtip_options(db):
    """
    Add tips to tposs table
    """
    await db.execute(
        """
        ALTER TABLE tpos.tposs ADD tip_options TEXT NULL;
    """
    )

async def m004_addwithdrawlimit(db):
    """
    Adds withdrawlimit and withdrawamt to tposs table
    """
    await db.execute(
        """
        ALTER TABLE tpos.tposs ADD COLUMN withdrawlimit INTEGER DEFAULT 0;
    """
    )
    await db.execute(
        """
        ALTER TABLE tpos.tposs ADD COLUMN withdrawpin INTEGER DEFAULT 878787;
    """
    )
    await db.execute(
        """
        ALTER TABLE tpos.tposs ADD COLUMN withdrawamt INTEGER DEFAULT 0;
    """
    )
    await db.execute(
        f"""
        ALTER TABLE tpos.tposs ADD COLUMN withdrawtime TIMESTAMP;
    """
    )

async def m005_initial(db):
    """
    Initial withdaws table.
    """
    await db.execute(
        f"""
        CREATE TABLE tpos.withdaws (
            id TEXT PRIMARY KEY,
            tpos_id TEXT NOT NULL,
            amount int NOT NULL,
            claimed BOOL NOT NULL
        );
    """
    )