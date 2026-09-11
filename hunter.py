import os
import sys
import time
import datetime
import oci

def log(msg):
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{timestamp}] {msg}", flush=True)

def main():
    log("=== ToolifyHub OCI ARM Capacity Hunter Started ===")

    # 1. Validate Environment Variables
    required_vars = [
        "OCI_USER_ID",
        "OCI_TENANCY_ID",
        "OCI_KEY_FINGERPRINT",
        "OCI_PRIVATE_KEY",
        "OCI_SUBNET_ID",
        "OCI_IMAGE_ID",
        "OCI_SSH_PUBLIC_KEY"
    ]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        log(f"FATAL: Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

    user_id = os.environ["OCI_USER_ID"]
    tenancy_id = os.environ["OCI_TENANCY_ID"]
    fingerprint = os.environ["OCI_KEY_FINGERPRINT"]
    region = os.environ.get("OCI_REGION", "ap-hyderabad-1")
    private_key = os.environ["OCI_PRIVATE_KEY"]
    subnet_id = os.environ["OCI_SUBNET_ID"]
    image_id = os.environ["OCI_IMAGE_ID"]
    ssh_pub_key = os.environ["OCI_SSH_PUBLIC_KEY"]

    target_ocpus = float(os.environ.get("OCI_OCPUS", "2.0"))
    target_memory = float(os.environ.get("OCI_MEMORY_IN_GBS", "12.0"))
    shape = "VM.Standard.A1.Flex"

    log(f"Target Region: {region}")
    log(f"Target Shape:  {shape} ({target_ocpus} OCPU / {target_memory} GB RAM)")
    log(f"Subnet OCID:   {subnet_id}")

    # 2. Configure OCI SDK directly in-memory (No private keys written to disk)
    config = {
        "user": user_id,
        "key_content": private_key,
        "fingerprint": fingerprint,
        "tenancy": tenancy_id,
        "region": region
    }
    try:
        oci.config.validate_config(config)
    except Exception as e:
        log(f"FATAL: OCI config validation failed: {e}")
        sys.exit(1)

    compute_client = oci.core.ComputeClient(config)
    identity_client = oci.identity.IdentityClient(config)
    net_client = oci.core.VirtualNetworkClient(config)

    # 3. Check if target A1 instance already exists
    log("Checking if active VM.Standard.A1.Flex instance already exists...")
    try:
        instances = compute_client.list_instances(compartment_id=tenancy_id).data
        for inst in instances:
            if inst.shape == shape and inst.lifecycle_state in ("RUNNING", "PROVISIONING", "STARTING"):
                log("=" * 60)
                log(f"ALREADY ACTIVE! An A1 instance already exists and is active:")
                log(f"Instance OCID: {inst.id}")
                log(f"Display Name:  {inst.display_name}")
                log(f"State:         {inst.lifecycle_state}")
                log("No further provisioning attempts needed. Exiting.")
                log("=" * 60)
                sys.exit(0)
    except Exception as e:
        log(f"WARNING: Could not list instances: {e}")

    # 4. Determine Availability Domains
    configured_ad = os.environ.get("OCI_AVAILABILITY_DOMAIN")
    if configured_ad:
        ad_list = [configured_ad]
    else:
        try:
            ads = identity_client.list_availability_domains(compartment_id=tenancy_id).data
            ad_list = [ad.name for ad in ads]
            log(f"Discovered {len(ad_list)} Availability Domain(s): {', '.join(ad_list)}")
        except Exception as e:
            log(f"FATAL: Failed to query Availability Domains: {e}")
            sys.exit(1)

    # 5. Attempt Instance Launch on each Availability Domain
    shape_config = oci.core.models.LaunchInstanceShapeConfigDetails(
        ocpus=target_ocpus,
        memory_in_gbs=target_memory
    )
    vnic_details = oci.core.models.CreateVnicDetails(
        subnet_id=subnet_id,
        assign_public_ip=True,
        display_name="primary-vnic"
    )

    for ad in ad_list:
        log(f"Attempting launch on Availability Domain: {ad}...")
        instance_details = oci.core.models.LaunchInstanceDetails(
            availability_domain=ad,
            compartment_id=tenancy_id,
            display_name="toolifyhub-word-pdf-engine-a1",
            image_id=image_id,
            shape=shape,
            shape_config=shape_config,
            create_vnic_details=vnic_details,
            metadata={
                "ssh_authorized_keys": ssh_pub_key
            }
        )

        try:
            response = compute_client.launch_instance(instance_details)
            instance = response.data
            log("=" * 60)
            log(f"🚀 SUCCESS! A1 INSTANCE CREATED!")
            log(f"Instance OCID: {instance.id}")
            log(f"State:         {instance.lifecycle_state}")
            log(f"AD:            {instance.availability_domain}")
            log("=" * 60)

            # Wait for RUNNING and fetch Public IP
            log("Waiting for instance to reach RUNNING state...")
            while instance.lifecycle_state not in ("RUNNING", "TERMINATED", "TERMINATING"):
                time.sleep(5)
                instance = compute_client.get_instance(instance.id).data

            public_ip = "Unknown"
            if instance.lifecycle_state == "RUNNING":
                log("Instance is RUNNING! Querying VNIC for Public IP...")
                vnics = compute_client.list_vnic_attachments(
                    compartment_id=tenancy_id,
                    instance_id=instance.id
                ).data
                if vnics:
                    vnic = net_client.get_vnic(vnics[0].vnic_id).data
                    public_ip = vnic.public_ip
                    log(f"Public IP:  {public_ip}")
                    log(f"Private IP: {vnic.private_ip}")

            log("=" * 60)
            log("VM IS FULLY ONLINE AND READY!")
            log(f"Instance OCID: {instance.id}")
            log(f"Public IP:     {public_ip}")
            log("=" * 60)
            sys.exit(0)

        except oci.exceptions.ServiceError as e:
            if "Out of host capacity" in str(e.message) or e.status == 500:
                log(f"Capacity Notice: Out of host capacity in {ad} (HTTP {e.status}).")
            elif e.status == 429:
                log(f"Rate Limit Notice: Too many requests (HTTP 429).")
            else:
                log(f"OCI ServiceError on {ad}: Code={e.code}, Status={e.status}, Message={e.message}")
                # If authentication or quota error, exit 1 to alert user
                if e.status in (400, 401, 403, 404):
                    log(f"FATAL: Permanent error returned by OCI API: {e.message}")
                    sys.exit(1)
        except Exception as ex:
            log(f"Unexpected error on {ad}: {type(ex).__name__}: {str(ex)}")

    log("STATUS: All Availability Domains currently at full host capacity. Gracefully exiting until next scheduled run.")
    sys.exit(0)

if __name__ == "__main__":
    main()
