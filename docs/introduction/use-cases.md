# Use Cases

Flux excels in scenarios that require reliable automation, complex orchestration, and fault-tolerant execution. Here are detailed use cases across various industries and domains.

## Data Processing Pipelines

### ETL/ELT Workflows
Transform raw data into actionable insights with robust error handling and state management.

**Example: Customer Data Analytics Pipeline**
```python
from flux import workflow, task, parallel

@task
def extract_customer_data(source: str) -> dict:
    # Extract from multiple sources: CRM, support tickets, website analytics
    return {
        "crm_data": fetch_from_crm(source),
        "support_data": fetch_from_support_system(source),
        "analytics_data": fetch_from_web_analytics(source)
    }

@task
def transform_customer_profile(raw_data: dict) -> dict:
    # Clean, normalize, and enrich customer data
    profile = normalize_customer_data(raw_data["crm_data"])
    profile = enrich_with_support_history(profile, raw_data["support_data"])
    profile = add_behavioral_insights(profile, raw_data["analytics_data"])
    return profile

@task
def load_to_data_warehouse(profiles: list[dict]) -> dict:
    # Load processed data with deduplication and validation
    loaded_count = bulk_insert_customers(profiles)
    return {"loaded_customers": loaded_count, "timestamp": datetime.now()}

@workflow
def customer_analytics_pipeline(date_range: str):
    # Extract data in parallel from all sources
    raw_data = parallel([
        extract_customer_data("crm"),
        extract_customer_data("support"),
        extract_customer_data("analytics")
    ])

    # Transform and load
    profiles = [transform_customer_profile(data) for data in raw_data]
    result = load_to_data_warehouse(profiles)

    # Generate analytics report
    return generate_customer_insights_report(result)
```

**Key Benefits:**
- **Fault Tolerance**: Automatically retries failed data extractions
- **State Persistence**: Resumes from last successful step if interrupted
- **Parallel Processing**: Extracts from multiple sources simultaneously
- **Error Recovery**: Falls back to cached data when sources are unavailable

### Real-time Stream Processing
Process continuous data streams with event-driven workflows.

**Example: Financial Transaction Monitoring**
```python
@task
def validate_transaction(transaction: dict) -> dict:
    # Real-time fraud detection and validation
    fraud_score = calculate_fraud_risk(transaction)
    return {**transaction, "fraud_score": fraud_score}

@task
def apply_business_rules(transaction: dict) -> dict:
    # Apply regulatory and business compliance rules
    compliance_result = check_aml_compliance(transaction)
    return {**transaction, "compliance": compliance_result}

@workflow
def transaction_processing_pipeline(transaction: dict):
    # Process transaction through validation pipeline
    validated = validate_transaction(transaction)

    if validated["fraud_score"] > 0.8:
        return flag_for_manual_review(validated)

    compliant = apply_business_rules(validated)

    if compliant["compliance"]["status"] == "approved":
        return process_payment(compliant)
    else:
        return reject_transaction(compliant)
```

### Batch Data Processing
Handle large-scale batch processing with memory efficiency and progress tracking.

**Example: Log Analysis Pipeline**
```python
@task
def process_log_batch(log_files: list[str], batch_size: int = 100) -> dict:
    # Process logs in memory-efficient batches
    processed_events = []

    for batch in chunk_files(log_files, batch_size):
        events = parse_log_files(batch)
        filtered_events = filter_relevant_events(events)
        processed_events.extend(filtered_events)

    return {"events": processed_events, "count": len(processed_events)}

@workflow
def daily_log_analysis(date: str):
    # Find all log files for the date
    log_files = discover_log_files(date)

    # Process in parallel batches
    batch_results = map_task(
        process_log_batch,
        chunk_files(log_files, 1000),  # 1000 files per batch
        concurrency=4
    )

    # Aggregate results
    total_events = sum(batch["count"] for batch in batch_results)

    # Generate daily report
    return generate_log_analysis_report(batch_results, total_events)
```

## Integration Workflows

### API Orchestration
Coordinate complex API interactions with retry logic and fallback mechanisms.

**Example: Multi-Service Order Processing**
```python
@task(retry=RetryConfig(max_attempts=3, backoff=BackoffStrategy.EXPONENTIAL))
def reserve_inventory(order: dict) -> dict:
    # Reserve items in inventory management system
    response = inventory_api.reserve_items(order["items"])
    return {"reservation_id": response["id"], "expires_at": response["expires_at"]}

@task(retry=RetryConfig(max_attempts=3))
def process_payment(order: dict, reservation: dict) -> dict:
    # Process payment through payment gateway
    payment_response = payment_api.charge_customer(
        order["customer_id"],
        order["total_amount"]
    )
    return {"payment_id": payment_response["transaction_id"]}

@task(fallback=FallbackConfig(handler=send_email_notification))
def send_sms_notification(order: dict) -> dict:
    # Send SMS confirmation (with email fallback)
    sms_response = sms_api.send_message(
        order["customer_phone"],
        f"Order {order['order_id']} confirmed!"
    )
    return {"notification_sent": True, "method": "sms"}

def send_email_notification(context, error) -> dict:
    # Fallback notification method
    order = context.input["order"]
    email_api.send_confirmation(order["customer_email"], order)
    return {"notification_sent": True, "method": "email"}

@workflow
def order_processing_workflow(order: dict):
    # Reserve inventory first
    reservation = reserve_inventory(order)

    try:
        # Process payment
        payment = process_payment(order, reservation)

        # Confirm order
        confirmed_order = confirm_order(order, payment, reservation)

        # Send notification (SMS with email fallback)
        notification = send_sms_notification(confirmed_order)

        return {
            "status": "completed",
            "order_id": confirmed_order["order_id"],
            "notification": notification
        }

    except PaymentError:
        # Release inventory reservation if payment fails
        release_inventory_reservation(reservation["reservation_id"])
        return {"status": "payment_failed", "order_id": order["order_id"]}
```

### Event-Driven Processes
Respond to external events with sophisticated workflow orchestration.

**Example: User Onboarding Automation**
```python
from flux import workflow, task, pause

@workflow
def user_onboarding_workflow(user_registration: dict):
    # Create user account
    user = create_user_account(user_registration)

    # Send welcome email
    welcome_email = send_welcome_email(user)

    # Pause for email verification (up to 24 hours)
    verification = await pause("email_verification")

    if verification.get("verified"):
        # User verified - continue onboarding
        profile = create_user_profile(user, verification["profile_data"])

        # Set up initial preferences
        preferences = setup_default_preferences(profile)

        # Send onboarding completion email
        completion_email = send_completion_email(user, profile)

        return {
            "status": "completed",
            "user_id": user["id"],
            "profile_id": profile["id"]
        }
    else:
        # Verification timeout - send reminder and extend
        send_verification_reminder(user)
        return {"status": "verification_pending", "user_id": user["id"]}
```

### Microservice Coordination
Orchestrate complex interactions between microservices with proper error handling.

**Example: Distributed E-commerce Checkout**
```python
@workflow
def checkout_workflow(cart: dict, customer: dict):
    # Validate cart and customer in parallel
    validation_results = parallel([
        validate_cart_items(cart),
        validate_customer_details(customer),
        check_shipping_availability(cart, customer["address"])
    ])

    cart_valid, customer_valid, shipping_available = validation_results

    if not all([cart_valid["valid"], customer_valid["valid"], shipping_available["available"]]):
        return {"status": "validation_failed", "errors": validation_results}

    # Calculate pricing
    pricing = calculate_final_pricing(cart, customer, shipping_available)

    # Begin transaction coordination
    transaction_id = begin_distributed_transaction()

    try:
        # Reserve inventory
        inventory_reservation = reserve_cart_inventory(cart, transaction_id)

        # Process payment
        payment_result = process_customer_payment(customer, pricing, transaction_id)

        # Create shipment
        shipment = create_shipment_order(cart, customer, transaction_id)

        # Update customer loyalty points
        loyalty_update = update_loyalty_points(customer, pricing)

        # Commit all operations
        commit_distributed_transaction(transaction_id)

        # Send confirmation
        send_order_confirmation(customer, cart, payment_result, shipment)

        return {
            "status": "success",
            "order_id": transaction_id,
            "payment_id": payment_result["id"],
            "shipment_id": shipment["id"]
        }

    except Exception as e:
        # Rollback all operations
        rollback_distributed_transaction(transaction_id)
        return {"status": "failed", "error": str(e), "transaction_id": transaction_id}
```

## Business Process Automation

### Document Processing Workflows
Automate document handling with human-in-the-loop approval processes.

**Example: Invoice Processing System**
```python
@task
def extract_invoice_data(document_path: str) -> dict:
    # Use OCR and AI to extract invoice data
    raw_text = perform_ocr(document_path)
    structured_data = extract_invoice_fields(raw_text)

    return {
        "vendor": structured_data["vendor"],
        "amount": structured_data["amount"],
        "due_date": structured_data["due_date"],
        "line_items": structured_data["line_items"],
        "confidence_score": structured_data["confidence"]
    }

@task
def validate_invoice_data(invoice_data: dict) -> dict:
    # Validate against business rules and vendor database
    validation_results = {
        "vendor_valid": validate_vendor(invoice_data["vendor"]),
        "amount_reasonable": validate_amount_range(invoice_data["amount"]),
        "duplicate_check": check_for_duplicates(invoice_data),
        "po_match": match_to_purchase_order(invoice_data)
    }

    return {
        **invoice_data,
        "validation": validation_results,
        "auto_approvable": all(validation_results.values())
    }

@workflow
def invoice_processing_workflow(document_path: str):
    # Extract data from invoice
    invoice_data = extract_invoice_data(document_path)

    # Validate extracted data
    validated_invoice = validate_invoice_data(invoice_data)

    if validated_invoice["auto_approvable"] and validated_invoice["confidence_score"] > 0.95:
        # Auto-approve high-confidence, valid invoices
        approval_result = auto_approve_invoice(validated_invoice)

        # Process payment
        payment_scheduled = schedule_payment(validated_invoice, approval_result)

        return {
            "status": "auto_approved",
            "invoice_id": approval_result["invoice_id"],
            "payment_date": payment_scheduled["payment_date"]
        }
    else:
        # Requires human review
        review_request = await pause("human_review", data={
            "invoice": validated_invoice,
            "issues": [k for k, v in validated_invoice["validation"].items() if not v]
        }, timeout=604800)  # 7 days

        if review_request.get("approved"):
            # Human approved - process payment
            payment_scheduled = schedule_payment(validated_invoice, review_request)
            return {
                "status": "human_approved",
                "reviewer": review_request["reviewer"],
                "payment_date": payment_scheduled["payment_date"]
            }
        else:
            # Rejected or timed out
            return {
                "status": "rejected",
                "reason": review_request.get("rejection_reason", "Review timeout")
            }
```

### Approval Workflows
Handle multi-step approval processes with escalation and delegation.

**Example: Expense Approval System**
```python
@workflow
def expense_approval_workflow(expense_report: dict):
    # Validate expense report
    validation = validate_expense_report(expense_report)

    if not validation["valid"]:
        return {"status": "rejected", "reason": validation["errors"]}

    # Determine approval hierarchy based on amount
    amount = expense_report["total_amount"]

    if amount <= 500:
        # Manager approval only
        manager_approval = request_manager_approval(expense_report)

        if manager_approval.get("approved"):
            return process_expense_payment(expense_report, [manager_approval])
        else:
            return {"status": "rejected", "reason": manager_approval.get("reason")}

    elif amount <= 5000:
        # Manager + Director approval
        manager_approval = request_manager_approval(expense_report)

        if not manager_approval.get("approved"):
            return {"status": "rejected", "reason": manager_approval.get("reason")}

        director_approval = request_director_approval(expense_report, manager_approval)

        if director_approval.get("approved"):
            return process_expense_payment(expense_report, [manager_approval, director_approval])
        else:
            return {"status": "rejected", "reason": director_approval.get("reason")}

    else:
        # Full executive approval chain
        approvals = []

        # Manager approval
        manager_approval = request_manager_approval(expense_report)
        if not manager_approval.get("approved"):
            return {"status": "rejected", "level": "manager", "reason": manager_approval.get("reason")}
        approvals.append(manager_approval)

        # Director approval
        director_approval = request_director_approval(expense_report, manager_approval)
        if not director_approval.get("approved"):
            return {"status": "rejected", "level": "director", "reason": director_approval.get("reason")}
        approvals.append(director_approval)

        # CFO approval for large amounts
        cfo_approval = request_cfo_approval(expense_report, approvals)
        if cfo_approval.get("approved"):
            approvals.append(cfo_approval)
            return process_expense_payment(expense_report, approvals)
        else:
            return {"status": "rejected", "level": "cfo", "reason": cfo_approval.get("reason")}

def request_manager_approval(expense_report: dict) -> dict:
    return await pause("manager_approval", data={"expense_report": expense_report, "approver_role": "manager"}, timeout=259200)  # 3 days

def request_director_approval(expense_report: dict, previous_approval: dict) -> dict:
    return await pause("director_approval", data={
        "expense_report": expense_report,
        "previous_approval": previous_approval,
        "approver_role": "director"
    }, timeout=432000)  # 5 days
```

## Machine Learning Operations (MLOps)

### Model Training Pipelines
Orchestrate complex ML training workflows with experiment tracking and model validation.

**Example: Automated Model Training Pipeline**
```python
@task
def prepare_training_data(dataset_config: dict) -> dict:
    # Download and prepare training data
    raw_data = download_dataset(dataset_config["source"])

    # Feature engineering
    features = engineer_features(raw_data, dataset_config["feature_config"])

    # Split data
    train_data, val_data, test_data = split_dataset(features, dataset_config["split_ratios"])

    return {
        "train_data": train_data,
        "validation_data": val_data,
        "test_data": test_data,
        "feature_schema": features["schema"]
    }

@task
def train_model(data: dict, model_config: dict) -> dict:
    # Train model with hyperparameter tuning
    best_model, metrics = train_with_hyperparameter_search(
        data["train_data"],
        data["validation_data"],
        model_config
    )

    return {
        "model": best_model,
        "training_metrics": metrics,
        "model_version": generate_model_version()
    }

@task
def validate_model(model_info: dict, test_data: dict, validation_criteria: dict) -> dict:
    # Comprehensive model validation
    performance_metrics = evaluate_model_performance(
        model_info["model"],
        test_data
    )

    # Bias and fairness testing
    bias_analysis = test_model_bias(model_info["model"], test_data)

    # Model explainability
    explainability_report = generate_explainability_report(
        model_info["model"],
        test_data
    )

    # Check if model meets criteria
    passes_validation = all([
        performance_metrics["accuracy"] >= validation_criteria["min_accuracy"],
        performance_metrics["f1_score"] >= validation_criteria["min_f1"],
        bias_analysis["fairness_score"] >= validation_criteria["min_fairness"]
    ])

    return {
        "performance": performance_metrics,
        "bias_analysis": bias_analysis,
        "explainability": explainability_report,
        "passes_validation": passes_validation
    }

@workflow
def ml_training_pipeline(experiment_config: dict):
    # Prepare data
    data = prepare_training_data(experiment_config["dataset"])

    # Train multiple model variants in parallel
    model_configs = experiment_config["model_variants"]
    trained_models = parallel([
        train_model(data, config) for config in model_configs
    ])

    # Validate all models
    validation_results = parallel([
        validate_model(model, data["test_data"], experiment_config["validation_criteria"])
        for model in trained_models
    ])

    # Select best model
    valid_models = [
        (model, validation) for model, validation in zip(trained_models, validation_results)
        if validation["passes_validation"]
    ]

    if not valid_models:
        return {"status": "no_valid_models", "experiment_id": experiment_config["id"]}

    # Choose model with best performance
    best_model, best_validation = max(
        valid_models,
        key=lambda x: x[1]["performance"]["f1_score"]
    )

    # Register model for deployment
    model_registration = register_model_for_deployment(
        best_model,
        best_validation,
        experiment_config
    )

    return {
        "status": "success",
        "best_model_version": best_model["model_version"],
        "performance_metrics": best_validation["performance"],
        "model_id": model_registration["model_id"]
    }
```

### Model Deployment Workflows
Automate model deployment with blue-green deployments and rollback capabilities.

**Example: Safe Model Deployment Pipeline**
```python
@workflow
def model_deployment_pipeline(model_deployment_request: dict):
    # Validate deployment readiness
    readiness_check = validate_deployment_readiness(model_deployment_request)

    if not readiness_check["ready"]:
        return {"status": "deployment_blocked", "issues": readiness_check["issues"]}

    # Deploy to staging environment
    staging_deployment = deploy_to_staging(model_deployment_request)

    # Run staging validation tests
    staging_tests = run_staging_validation_tests(staging_deployment)

    if not staging_tests["passed"]:
        return {
            "status": "staging_failed",
            "test_results": staging_tests,
            "deployment_id": staging_deployment["id"]
        }

    # Deploy to production with blue-green strategy
    production_deployment = deploy_to_production_blue_green(model_deployment_request)

    # Gradual traffic rollout
    rollout_phases = [10, 25, 50, 100]  # Percentage of traffic

    for phase_percentage in rollout_phases:
        # Route percentage of traffic to new model
        traffic_split = update_traffic_routing(
            production_deployment,
            phase_percentage
        )

        # Monitor performance for 15 minutes
        monitoring_result = await pause("monitoring_check", data={
            "deployment": production_deployment,
            "traffic_percentage": phase_percentage
        }, timeout=900)  # 15 minutes

        # Check if monitoring detected issues
        if monitoring_result.get("issues_detected"):
            # Rollback deployment
            rollback_result = rollback_deployment(production_deployment)
            return {
                "status": "rolled_back",
                "phase": phase_percentage,
                "issues": monitoring_result["issues"],
                "rollback_id": rollback_result["id"]
            }

    # Full deployment successful
    cleanup_old_deployment(production_deployment["previous_version"])

    return {
        "status": "deployment_complete",
        "model_version": model_deployment_request["model_version"],
        "deployment_id": production_deployment["id"]
    }
```

## Infrastructure Automation

### CI/CD Pipelines
Orchestrate complex deployment pipelines with testing, security scanning, and rollback capabilities.

**Example: Full-Stack Application Deployment**
```python
@workflow
def application_deployment_pipeline(deployment_request: dict):
    # Code quality and security checks
    quality_checks = parallel([
        run_unit_tests(deployment_request["source_code"]),
        run_integration_tests(deployment_request["source_code"]),
        run_security_scan(deployment_request["source_code"]),
        run_code_quality_analysis(deployment_request["source_code"])
    ])

    # Check if all quality gates passed
    all_checks_passed = all(check["passed"] for check in quality_checks)

    if not all_checks_passed:
        return {
            "status": "quality_gates_failed",
            "failed_checks": [c for c in quality_checks if not c["passed"]]
        }

    # Build application artifacts
    build_results = parallel([
        build_frontend_assets(deployment_request),
        build_backend_services(deployment_request),
        build_database_migrations(deployment_request)
    ])

    # Deploy to staging environment
    staging_deployment = deploy_to_staging_environment(build_results)

    # Run comprehensive staging tests
    staging_test_results = parallel([
        run_end_to_end_tests(staging_deployment),
        run_performance_tests(staging_deployment),
        run_load_tests(staging_deployment)
    ])

    if not all(test["passed"] for test in staging_test_results):
        return {
            "status": "staging_tests_failed",
            "test_results": staging_test_results,
            "staging_deployment": staging_deployment
        }

    # Production deployment with canary release
    production_deployment = initiate_canary_deployment(build_results)

    # Monitor canary deployment
    canary_monitoring = monitor_canary_deployment(
        production_deployment,
        duration=1800  # 30 minutes
    )

    if canary_monitoring["healthy"]:
        # Promote canary to full production
        full_deployment = promote_canary_to_production(production_deployment)

        # Clean up old version
        cleanup_previous_deployment(full_deployment["previous_version"])

        return {
            "status": "deployment_successful",
            "deployment_id": full_deployment["id"],
            "version": deployment_request["version"]
        }
    else:
        # Rollback canary deployment
        rollback_result = rollback_canary_deployment(production_deployment)

        return {
            "status": "canary_failed",
            "issues": canary_monitoring["issues"],
            "rollback_id": rollback_result["id"]
        }
```

### Disaster Recovery Workflows
Automate disaster recovery procedures with comprehensive testing and validation.

**Example: Database Disaster Recovery**
```python
@workflow
def database_disaster_recovery_workflow(disaster_event: dict):
    # Assess damage and determine recovery strategy
    damage_assessment = assess_disaster_damage(disaster_event)

    if damage_assessment["severity"] == "minor":
        # Minor issues - attempt repair
        repair_result = attempt_database_repair(damage_assessment)

        if repair_result["successful"]:
            return {
                "status": "repaired",
                "downtime": repair_result["downtime"],
                "repair_actions": repair_result["actions"]
            }

    # Major disaster - full recovery procedure
    recovery_plan = generate_recovery_plan(damage_assessment)

    # Failover to backup systems
    failover_result = initiate_failover_to_backup(recovery_plan)

    # Restore from latest backup
    backup_restore = parallel([
        restore_database_from_backup(recovery_plan["primary_backup"]),
        restore_file_storage_from_backup(recovery_plan["storage_backup"]),
        restore_configuration_from_backup(recovery_plan["config_backup"])
    ])

    # Validate restored systems
    validation_results = parallel([
        validate_database_integrity(backup_restore[0]),
        validate_application_functionality(backup_restore),
        validate_data_consistency(backup_restore)
    ])

    if all(v["valid"] for v in validation_results):
        # Switch traffic to recovered systems
        traffic_switch = switch_traffic_to_recovered_systems(backup_restore)

        # Begin rebuilding primary systems
        rebuild_task = initiate_primary_system_rebuild(disaster_event)

        return {
            "status": "recovery_complete",
            "failover_time": failover_result["completion_time"],
            "data_loss": validation_results[2]["data_loss_minutes"],
            "rebuild_task_id": rebuild_task["task_id"]
        }
    else:
        # Recovery validation failed
        return {
            "status": "recovery_failed",
            "validation_failures": [v for v in validation_results if not v["valid"]]
        }
```

## Industry-Specific Applications

### Healthcare: Patient Care Workflows
Manage complex patient care processes with compliance and audit requirements.

**Example: Patient Admission and Treatment Workflow**
```python
@workflow
def patient_admission_workflow(patient_data: dict):
    # Verify insurance and eligibility
    insurance_verification = verify_patient_insurance(patient_data)

    if not insurance_verification["covered"]:
        return {
            "status": "admission_denied",
            "reason": "insurance_not_verified",
            "details": insurance_verification
        }

    # Create patient record
    patient_record = create_patient_record(patient_data, insurance_verification)

    # Assign medical team
    team_assignment = assign_medical_team(patient_record)

    # Initial medical assessment
    initial_assessment = await pause("initial_assessment", data={
        "patient": patient_record,
        "assigned_team": team_assignment
    }, timeout=7200)  # 2 hours

    # Create treatment plan based on assessment
    treatment_plan = create_treatment_plan(
        patient_record,
        initial_assessment["assessment_data"]
    )

    # Schedule required procedures
    procedure_scheduling = schedule_medical_procedures(
        treatment_plan["required_procedures"]
    )

    # Begin treatment monitoring
    monitoring_workflow = initiate_patient_monitoring(
        patient_record,
        treatment_plan
    )

    return {
        "status": "admission_complete",
        "patient_id": patient_record["id"],
        "treatment_plan_id": treatment_plan["id"],
        "monitoring_workflow_id": monitoring_workflow["id"]
    }
```

### Finance: Risk Management Workflows
Implement sophisticated risk assessment and mitigation processes.

**Example: Credit Risk Assessment Pipeline**
```python
@workflow
def credit_risk_assessment_workflow(loan_application: dict):
    # Gather data from multiple sources
    applicant_data = parallel([
        fetch_credit_bureau_data(loan_application["ssn"]),
        fetch_employment_verification(loan_application["employer"]),
        fetch_bank_statements(loan_application["bank_accounts"]),
        fetch_property_valuation(loan_application.get("collateral"))
    ])

    credit_data, employment_data, financial_data, collateral_data = applicant_data

    # Run risk models
    risk_assessments = parallel([
        calculate_credit_score_model(credit_data, financial_data),
        calculate_income_stability_model(employment_data, financial_data),
        calculate_collateral_risk_model(collateral_data),
        calculate_fraud_risk_model(loan_application, credit_data)
    ])

    credit_risk, income_risk, collateral_risk, fraud_risk = risk_assessments

    # Aggregate risk assessment
    overall_risk = aggregate_risk_scores(risk_assessments)

    # Apply business rules
    decision_matrix = apply_lending_decision_rules(
        loan_application,
        overall_risk,
        risk_assessments
    )

    if decision_matrix["auto_approve"]:
        # Auto-approve low-risk applications
        approval_result = auto_approve_loan(
            loan_application,
            decision_matrix["approved_terms"]
        )

        return {
            "status": "auto_approved",
            "loan_id": approval_result["loan_id"],
            "terms": approval_result["terms"],
            "risk_score": overall_risk["score"]
        }

    elif decision_matrix["auto_reject"]:
        # Auto-reject high-risk applications
        return {
            "status": "auto_rejected",
            "risk_score": overall_risk["score"],
            "rejection_reasons": decision_matrix["rejection_reasons"]
        }

    else:
        # Requires manual underwriter review
        underwriter_review = await pause("underwriter_review", data={
            "application": loan_application,
            "risk_assessment": overall_risk,
            "detailed_assessments": risk_assessments,
            "recommendation": decision_matrix["recommendation"]
        }, timeout=172800)  # 48 hours

        if underwriter_review.get("approved"):
            approval_result = approve_loan_with_conditions(
                loan_application,
                underwriter_review["approved_terms"],
                underwriter_review["conditions"]
            )

            return {
                "status": "manually_approved",
                "loan_id": approval_result["loan_id"],
                "underwriter": underwriter_review["underwriter_id"],
                "conditions": underwriter_review["conditions"]
            }
        else:
            return {
                "status": "manually_rejected",
                "underwriter": underwriter_review["underwriter_id"],
                "rejection_reason": underwriter_review["rejection_reason"]
            }
```

## Choosing Flux for Your Use Case

### Ideal Scenarios for Flux

**✅ Choose Flux when you need:**
- Complex multi-step processes with dependencies
- Reliable execution with fault tolerance
- Long-running workflows that need state persistence
- Human-in-the-loop approval processes
- Integration between multiple systems/APIs
- Detailed audit trails and monitoring
- Developer-friendly workflow definition
- Scalable execution across multiple workers

**✅ Flux excels at:**
- Data pipeline orchestration
- Business process automation
- API orchestration and integration
- ML/AI workflow management
- Document processing workflows
- Approval and review processes
- Disaster recovery automation
- Complex deployment pipelines

### When to Consider Alternatives

**❌ Flux might not be the best choice for:**
- Simple, single-step automations (consider task queues like Celery)
- Real-time stream processing (consider Apache Kafka, Pulsar)
- Event sourcing architectures (consider EventStore, Axon)
- Simple scheduled jobs (consider cron, APScheduler)
- UI-driven workflow design requirements (consider Airflow, Prefect UI)

## Next Steps

Ready to implement your use case with Flux?

1. **[Installation Guide](../getting-started/installation.md)**: Set up your development environment
2. **[Quick Start](../getting-started/quick-start-guide.md)**: Build your first workflow
3. **[Core Concepts](../core-concepts/workflow-management.md)**: Understand workflow design principles
4. **[Examples](../examples/basic.md)**: Explore code examples similar to your use case
5. **[CLI Reference](../cli/index.md)**: Master the development and deployment tools

Have questions about whether Flux fits your specific use case? Check our [GitHub Discussions](https://github.com/edurdias/flux/discussions) or explore the [community examples](https://github.com/edurdias/flux/tree/main/examples).
