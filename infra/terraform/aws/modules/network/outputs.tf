output "vpc_id" {
  value = aws_vpc.this.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "ecs_sg_id" {
  value = aws_security_group.ecs.id
}

output "opensearch_sg_id" {
  value = aws_security_group.opensearch.id
}
