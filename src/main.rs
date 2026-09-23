use serde_json::Value;
use std::collections::HashSet;
use std::env;
use std::error::Error;
use std::io::Read;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

type Result<T> = std::result::Result<T, Box<dyn Error>>;

#[derive(Debug)]
struct Config {
    machine_id: u64,
    dry_run: bool,
    poll_seconds: u64,
    min_peers: usize,
    offer_limit: usize,
    min_price: f64,
    max_price: f64,
    min_change: f64,
    disk_price: f64,
    upload_price: f64,
    download_price: f64,
    min_bid_price: f64,
    discount_rate: f64,
    min_chunk: u64,
    volume_size_gb: u64,
    volume_price: f64,
    duration_days: u64,
}

fn required<T: std::str::FromStr>(key: &str) -> Result<T>
where
    T::Err: Error + 'static,
{
    Ok(env::var(key)
        .map_err(|_| format!("missing {key}"))?
        .parse::<T>()?)
}

fn positive(key: &str) -> Result<f64> {
    let value: f64 = required(key)?;
    if !value.is_finite() || value < 0.0 {
        return Err(format!("{key} must be a finite nonnegative number").into());
    }
    Ok(value)
}

impl Config {
    fn load() -> Result<Self> {
        let dry_run = match env::var("DRY_RUN").as_deref() {
            Ok("false") => false,
            Ok("true") | Err(_) => true,
            _ => return Err("DRY_RUN must be true or false".into()),
        };
        if env::var("VAST_API_KEY").is_err() || env::var("VAST_API_KEY")?.trim().is_empty() {
            return Err("VAST_API_KEY is required".into());
        }
        let config = Self {
            machine_id: required("MACHINE_ID")?,
            dry_run,
            poll_seconds: required("POLL_SECONDS")?,
            min_peers: required("MIN_PEERS")?,
            offer_limit: required("OFFER_LIMIT")?,
            min_price: positive("MIN_PRICE")?,
            max_price: positive("MAX_PRICE")?,
            min_change: positive("MIN_CHANGE")?,
            disk_price: positive("DISK_PRICE")?,
            upload_price: positive("UPLOAD_PRICE")?,
            download_price: positive("DOWNLOAD_PRICE")?,
            min_bid_price: positive("MIN_BID_PRICE")?,
            discount_rate: positive("DISCOUNT_RATE")?,
            min_chunk: required("MIN_CHUNK")?,
            volume_size_gb: required("VOLUME_SIZE_GB")?,
            volume_price: positive("VOLUME_PRICE")?,
            duration_days: required("DURATION_DAYS")?,
        };
        if config.machine_id == 0
            || config.poll_seconds < 60
            || config.min_peers < 3
            || config.offer_limit < config.min_peers
            || config.offer_limit > 1000
            || config.min_price <= 0.0
            || config.max_price < config.min_price
            || config.min_change <= 0.0
            || config.min_chunk == 0
            || config.discount_rate > 1.0
            || config.min_bid_price > config.min_price
            || config.volume_size_gb == 0
            || !(2..=365).contains(&config.duration_days)
        {
            return Err(
                "invalid pricing, interval, peer count, bid floor or listing settings".into(),
            );
        }
        Ok(config)
    }
}

// The CLI uses Python; never pass the API key in argv or log its output on errors.
fn vastai(args: &[String]) -> Result<Value> {
    let mut child = Command::new("vastai")
        .args(args)
        .args(["--raw", "--retry", "1"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let mut stdout = child.stdout.take().ok_or("missing CLI stdout")?;
    let mut stderr = child.stderr.take().ok_or("missing CLI stderr")?;
    let out = thread::spawn(move || {
        let mut buf = Vec::new();
        stdout.read_to_end(&mut buf).map(|_| buf)
    });
    let err = thread::spawn(move || {
        let mut buf = Vec::new();
        stderr.read_to_end(&mut buf).map(|_| buf)
    });
    let deadline = Instant::now() + Duration::from_secs(45);
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline {
            child.kill()?;
            child.wait()?;
            return Err(format!("vastai {} timed out", args.join(" ")).into());
        }
        thread::sleep(Duration::from_millis(100));
    };
    let output = out.join().map_err(|_| "stdout reader panicked")??;
    let error = err.join().map_err(|_| "stderr reader panicked")??;
    if !status.success() {
        // CLI diagnostics sometimes contain request details, including credentials.
        return Err(format!(
            "vastai {} failed (status {status}); stderr {} bytes",
            args.join(" "),
            error.len()
        )
        .into());
    }
    Ok(serde_json::from_slice(&output)?)
}

fn number(row: &Value, field: &str) -> Result<f64> {
    let value = row[field]
        .as_f64()
        .ok_or_else(|| format!("missing numeric {field}"))?;
    if !value.is_finite() || value < 0.0 {
        return Err(format!("invalid {field}").into());
    }
    Ok(value)
}

fn machine<'a>(data: &'a Value, config: &Config) -> Result<&'a Value> {
    data.get("machines")
        .and_then(Value::as_array)
        .ok_or("missing machines array")?
        .iter()
        .find(|row| row["machine_id"].as_u64() == Some(config.machine_id))
        .ok_or_else(|| format!("machine {} not found in your account", config.machine_id).into())
}

fn peer_prices(data: &Value, own: &Value, config: &Config) -> Result<Vec<f64>> {
    let offers = data
        .as_array()
        .or_else(|| data.get("offers").and_then(Value::as_array))
        .ok_or("expected offers array")?;
    if offers.len() >= config.offer_limit {
        return Err(
            "market results reached OFFER_LIMIT; increase it to avoid a truncated median".into(),
        );
    }
    let name = own["gpu_name"].as_str().ok_or("machine missing gpu_name")?;
    let count = own["num_gpus"].as_u64().ok_or("machine missing num_gpus")?;
    let mut seen = HashSet::new();
    let mut prices = Vec::new();
    for offer in offers {
        let Some(id) = offer["machine_id"].as_u64() else {
            continue;
        };
        if id == config.machine_id
            || offer["gpu_name"].as_str() != Some(name)
            || offer["num_gpus"].as_u64() != Some(count)
            || offer["rentable"].as_bool() != Some(true)
            || offer["verification"].as_str() != Some("verified")
        {
            continue;
        }
        // dph_base is GPU-only $/hr for the offer; dph_total includes disk charges.
        if let Some(base) = offer["dph_base"].as_f64()
            && base.is_finite()
            && base > 0.0
            && seen.insert(id)
        {
            prices.push(base / count as f64);
        }
    }
    Ok(prices)
}

fn target_price(mut prices: Vec<f64>, config: &Config) -> Result<f64> {
    if prices.len() < config.min_peers {
        return Err(format!(
            "only {} comparable offers (need {})",
            prices.len(),
            config.min_peers
        )
        .into());
    }
    prices.sort_by(f64::total_cmp);
    let mid = prices.len() / 2;
    let median = if prices.len().is_multiple_of(2) {
        (prices[mid - 1] + prices[mid]) / 2.0
    } else {
        prices[mid]
    };
    let cents = (median.clamp(config.min_price, config.max_price) * 100.0).round() / 100.0;
    Ok(cents.clamp(config.min_price, config.max_price))
}

fn differs(row: &Value, field: &str, desired: f64) -> bool {
    row[field]
        .as_f64()
        .is_none_or(|actual| (actual - desired).abs() > 0.000_001)
}

fn listing_changed(own: &Value, target: f64, config: &Config, now: u64) -> bool {
    // Refresh the rolling listing when less than (duration - 1 day) remains.
    let refresh_at = now.saturating_add((config.duration_days - 1) * 86_400);
    own["listed"] != true
        || differs(own, "listed_gpu_cost", target)
        || differs(own, "listed_storage_cost", config.disk_price)
        || differs(own, "listed_inet_up_cost", config.upload_price)
        || differs(own, "listed_inet_down_cost", config.download_price)
        || differs(own, "min_bid_price", config.min_bid_price)
        || differs(own, "credit_discount_max", config.discount_rate)
        || differs(own, "listed_volume_cost", config.volume_price)
        || own["listed_min_gpu_count"].as_u64() != Some(config.min_chunk)
        || own["volume_total_size"].as_u64() != Some(config.volume_size_gb)
        || own["end_date"]
            .as_f64()
            .is_none_or(|end| end < refresh_at as f64)
}

fn cycle(config: &Config) -> Result<()> {
    let data = vastai(&["show".into(), "machines".into()])?;
    let own = machine(&data, config)?;
    if own["listed"] != true {
        return Err("machine is not listed; list it manually before running the service".into());
    }
    let gpu_name = own["gpu_name"].as_str().ok_or("machine missing GPU name")?;
    let count = own["num_gpus"]
        .as_u64()
        .ok_or("machine missing GPU count")?;
    if count == 0 || config.min_chunk > count {
        return Err("invalid GPU count or MIN_CHUNK".into());
    }
    let query = format!(
        "gpu_name={} num_gpus={} verified=true rentable=true",
        gpu_name.replace(' ', "_"),
        count
    );
    let market = vastai(&[
        "search".into(),
        "offers".into(),
        query,
        "--limit".into(),
        config.offer_limit.to_string(),
        "-o".into(),
        "dph".into(),
    ])?;
    let peers = peer_prices(&market, own, config)?;
    let sample_size = peers.len();
    let target = target_price(peers, config)?;
    let current = number(own, "listed_gpu_cost")?;
    let price_changed =
        (target - current).abs() + 0.000_001 >= config.min_change || current < config.min_bid_price;
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let settings_changed = listing_changed(own, current, config, now);
    if !price_changed && !settings_changed {
        println!(
            "machine={} peers={} current=${current:.4} target=${target:.4} no change",
            config.machine_id, sample_size
        );
        return Ok(());
    }
    let target = if price_changed { target } else { current };
    println!(
        "machine={} peers={} current=${current:.4} target=${target:.4} listing_changed={} dry_run={}",
        config.machine_id, sample_size, settings_changed, config.dry_run
    );
    if config.dry_run {
        return Ok(());
    }
    let args = vec![
        "list".into(),
        "machine".into(),
        config.machine_id.to_string(),
        "-g".into(),
        target.to_string(),
        "-s".into(),
        config.disk_price.to_string(),
        "-u".into(),
        config.upload_price.to_string(),
        "-d".into(),
        config.download_price.to_string(),
        "-b".into(),
        config.min_bid_price.to_string(),
        "-r".into(),
        config.discount_rate.to_string(),
        "-m".into(),
        config.min_chunk.to_string(),
        "-v".into(),
        config.volume_size_gb.to_string(),
        "-z".into(),
        config.volume_price.to_string(),
        "-l".into(),
        format!("{} days", config.duration_days),
    ];
    let response = vastai(&args)?;
    if response["success"].as_bool() != Some(true) {
        return Err("vastai list machine did not report success".into());
    }
    println!("machine={} listing updated", config.machine_id);
    Ok(())
}

fn main() -> Result<()> {
    let config = Config::load()?;
    println!(
        "pricing service started for machine={} dry_run={}",
        config.machine_id, config.dry_run
    );
    loop {
        if let Err(error) = cycle(&config) {
            eprintln!("pricing cycle failed: {error}");
        }
        thread::sleep(Duration::from_secs(config.poll_seconds));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn config() -> Config {
        Config {
            machine_id: 42,
            dry_run: true,
            poll_seconds: 1800,
            min_peers: 3,
            offer_limit: 100,
            min_price: 0.35,
            max_price: 0.8,
            min_change: 0.01,
            disk_price: 0.15,
            upload_price: 0.01,
            download_price: 0.01,
            min_bid_price: 0.3,
            discount_rate: 0.1,
            min_chunk: 1,
            volume_size_gb: 200,
            volume_price: 0.15,
            duration_days: 7,
        }
    }

    #[test]
    fn chooses_bounded_median_of_distinct_verified_rentable_peers() {
        let own = json!({"gpu_name":"RTX 5090", "num_gpus":1});
        let offers = json!([
            {"machine_id":42,"gpu_name":"RTX 5090","num_gpus":1,"rentable":true,"verification":"verified","dph_base":0.01},
            {"machine_id":1,"gpu_name":"RTX 5090","num_gpus":1,"rentable":true,"verification":"verified","dph_base":0.42,"dph_total":9.0},
            {"machine_id":1,"gpu_name":"RTX 5090","num_gpus":1,"rentable":true,"verification":"verified","dph_base":0.01},
            {"machine_id":2,"gpu_name":"RTX 5090","num_gpus":1,"rentable":true,"verification":"verified","dph_base":0.50},
            {"machine_id":3,"gpu_name":"RTX 5090","num_gpus":1,"rentable":true,"verification":"verified","dph_base":0.75},
            {"machine_id":4,"gpu_name":"RTX 5090","num_gpus":1,"rentable":false,"verification":"verified","dph_base":0.01}
        ]);
        let prices = peer_prices(&offers, &own, &config()).unwrap();
        assert_eq!(prices.len(), 3);
        assert_eq!(target_price(prices, &config()).unwrap(), 0.5);
        assert!(target_price(vec![0.2], &config()).is_err());
        assert_eq!(
            target_price(vec![0.01, 0.02, 0.03], &config()).unwrap(),
            0.35
        );
        let mut limited = config();
        limited.offer_limit = 6;
        assert!(peer_prices(&offers, &own, &limited).is_err());
    }

    #[test]
    fn detects_changes_to_non_gpu_listing_fields() {
        let own = json!({"listed":true,"listed_gpu_cost":0.5,"listed_storage_cost":0.15,
            "listed_inet_up_cost":0.01,"listed_inet_down_cost":0.01,"min_bid_price":0.3,
            "credit_discount_max":0.1,"listed_volume_cost":0.15,"listed_min_gpu_count":1,
            "volume_total_size":200,"end_date":2_000_000_000.0});
        let now = 2_000_000_000 - 7 * 86_400;
        assert!(!listing_changed(&own, 0.5, &config(), now));
        let mut changed = own.clone();
        changed["listed_volume_cost"] = json!(0.2);
        assert!(listing_changed(&changed, 0.5, &config(), now));
        assert!(listing_changed(&own, 0.5, &config(), now + 86_401));
    }
}
